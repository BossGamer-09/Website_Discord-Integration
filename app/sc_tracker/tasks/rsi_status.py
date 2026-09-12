import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone as dj_timezone
from django_celery_beat.models import IntervalSchedule

from app.celerytools.utils import register_periodic_task
from app.preferences.utils import get_global_preference
from app.sc_tracker import preferences as sc_prefs_module

logger = get_task_logger(__name__)

RSI_STATUS_JSON = "https://status.robertsspaceindustries.com/index.json"
PUBSUB_CHANNEL  = "sc_tracker:rsi_events"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _get_pref(cls):
    return get_global_preference(cls.import_path)


def _publish(events: list[dict]):
    try:
        from django.core.cache import caches
        redis_client = caches["default"].client.get_client()
        redis_client.publish(PUBSUB_CHANNEL, json.dumps(events))
    except Exception as exc:
        logger.warning("[RSI] Redis publish failed: %s", exc)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value or value.lower() == "<no value>":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


_RSI_ISSUE_BASE = "https://status.robertsspaceindustries.com/issues/"


def _fetch_issue_detail(filename: str) -> dict:
    """Fetch the individual issue index.json for body content and clean permalink."""
    slug = filename.replace(".md", "")
    try:
        resp = requests.get(
            f"{_RSI_ISSUE_BASE}{slug}/index.json",
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("[RSI] Failed to fetch issue detail for %s: %s", filename, exc)
        return {}


def _html_to_text(html: str) -> str:
    """Strip HTML tags, collapse whitespace, keep structure readable."""
    import re
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    html = re.sub(r'<strong>(.*?)</strong>', r'**\1**', html, flags=re.DOTALL)
    html = re.sub(r'<[^>]+>', '', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


def _normalize_issue(raw: dict) -> dict:
    filename = raw.get("filename", "")
    detail   = _fetch_issue_detail(filename) if filename else {}

    # Use the detail permalink (clean, no index.html) if available
    permalink = (
        detail.get("permalink") or raw.get("permalink", "")
    ).replace("index.html", "").replace("index.xml", "").rstrip("/") + "/"

    body_html = detail.get("body", "")
    markdown_content = _html_to_text(body_html) if body_html else ""

    # lastMod from detail may be more accurate
    lastmod = detail.get("lastMod") or raw.get("lastMod", "")

    return {
        "filename":         filename,
        "title":            raw.get("title", "Unknown Issue"),
        "affected":         raw.get("affected", []),
        "severity":         raw.get("severity", "unknown"),
        "permalink":        permalink,
        "kind":             raw.get("is", "issue"),
        "resolved":         bool(raw.get("resolved", False)),
        "informational":    bool(raw.get("informational", False)),
        "rsi_created_at":   _parse_dt(raw.get("createdAt", "")),
        "rsi_resolved_at":  _parse_dt(raw.get("resolvedAt", "")),
        "rsi_lastmod_at":   _parse_dt(lastmod),
        "markdown_content": markdown_content,
    }


@register_periodic_task(every=60, interval=IntervalSchedule.SECONDS, name="sc-tracker: Poll RSI Status")
@shared_task(name="app.sc_tracker.tasks.rsi_status.poll_rsi_status", bind=True, max_retries=3)
def poll_rsi_status(self):
    """Single source of RSI data. Fetches JSON, upserts DB cache, publishes Redis events."""
    from app.sc_tracker.models import RSIIssue, RSIStatusSnapshot

    if not _get_pref(sc_prefs_module.StatusEnabled):
        return

    try:
        resp = requests.get(RSI_STATUS_JSON, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("[RSI] Fetch failed: %s", exc)
        raise self.retry(exc=exc, countdown=30)

    RSIStatusSnapshot.objects.update_or_create(id=1, defaults={"raw_json": data})

    current_filenames: set[str] = set()
    events: list[dict] = []

    for system in data.get("systems", []):
        for raw_issue in system.get("unresolvedIssues", []):
            filename = raw_issue.get("filename", "")
            if not filename:
                continue

            current_filenames.add(filename)
            normalized = _normalize_issue(raw_issue)
            db_issue = RSIIssue.objects.filter(filename=filename).first()

            if not db_issue:
                db_issue = RSIIssue.objects.create(**normalized)
                logger.info("[RSI] New issue: %s", db_issue.title)
                events.append({"type": "new_issue", "filename": filename, "title": db_issue.title, "severity": db_issue.severity, "permalink": db_issue.permalink})
            else:
                changed = (
                    db_issue.resolved          != normalized["resolved"]
                    or db_issue.title          != normalized["title"]
                    or db_issue.severity       != normalized["severity"]
                    or str(db_issue.affected)  != str(normalized["affected"])
                    or db_issue.markdown_content != normalized["markdown_content"]
                    or (normalized["rsi_lastmod_at"] and db_issue.rsi_lastmod_at and normalized["rsi_lastmod_at"] > db_issue.rsi_lastmod_at)
                )
                if changed:
                    for field, value in normalized.items():
                        setattr(db_issue, field, value)
                    db_issue.save()
                    events.append({"type": "updated_issue", "filename": filename, "title": db_issue.title, "severity": db_issue.severity, "resolved": db_issue.resolved, "permalink": db_issue.permalink})

    for stale in RSIIssue.objects.filter(resolved=False).exclude(filename__in=current_filenames):
        stale.resolved = True
        stale.rsi_resolved_at = dj_timezone.now()
        stale.save(update_fields=["resolved", "rsi_resolved_at", "updated_at"])
        events.append({"type": "resolved_issue", "filename": stale.filename, "title": stale.title})

    if events:
        _publish(events)
        dispatch_status_dms.delay(events)

    logger.info("[RSI] Poll complete — %d current issues, %d events.", len(current_filenames), len(events))


@shared_task(name="app.sc_tracker.tasks.rsi_status.dispatch_status_dms")
def dispatch_status_dms(events: list[dict]):
    """Publish per-user DM send instructions to Redis. Bot cog does the actual send."""
    from app.sc_tracker.models import StatusDMMessage, StatusSubscription

    active_subs = list(
        StatusSubscription.objects.filter(is_active=True).values_list("discord_user_id", flat=True)
    )
    if not active_subs:
        return

    for event in events:
        if event.get("type") not in ("new_issue", "updated_issue", "resolved_issue"):
            continue

        hours = (
            _get_pref(sc_prefs_module.StatusNewDedupeHours)
            if event["type"] == "new_issue"
            else _get_pref(sc_prefs_module.StatusUpdateDedupeHours)
        )
        cutoff = dj_timezone.now() - timedelta(hours=hours)
        guid   = event.get("filename", "")

        for user_id in active_subs:
            already_sent = StatusDMMessage.objects.filter(
                discord_user_id=user_id,
                incident_guid=guid,
                message_type="update",
                created_at__gt=cutoff,
            ).exists()
            if not already_sent:
                _publish([{"type": "send_dm", "discord_user_id": user_id, "event": event}])


@register_periodic_task(every=1, interval=IntervalSchedule.HOURS, name="sc-tracker: Cleanup Status DMs")
@shared_task(name="app.sc_tracker.tasks.rsi_status.cleanup_status_dms")
def cleanup_status_dms():
    """Hourly: delete old DM records and tell the bot to delete the Discord messages."""
    from app.sc_tracker.models import RSIIssue, StatusDMMessage

    cutoff = dj_timezone.now() - timedelta(hours=_get_pref(sc_prefs_module.StatusDmCleanupHours))

    old = list(StatusDMMessage.objects.filter(created_at__lt=cutoff).values("id", "discord_user_id", "dm_message_id"))
    if old:
        _publish([{"type": "delete_dms", "messages": old}])
        StatusDMMessage.objects.filter(id__in=[m["id"] for m in old]).delete()

    resolved_filenames = list(RSIIssue.objects.filter(resolved=True).values_list("filename", flat=True))
    if resolved_filenames:
        resolved_msgs = list(StatusDMMessage.objects.filter(incident_guid__in=resolved_filenames).values("id", "discord_user_id", "dm_message_id"))
        if resolved_msgs:
            _publish([{"type": "delete_dms", "messages": resolved_msgs}])
            StatusDMMessage.objects.filter(id__in=[m["id"] for m in resolved_msgs]).delete()

    logger.info("[RSI] Cleanup complete.")