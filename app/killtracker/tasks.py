"""Celery tasks: RSI scrape + event persist, expired-key cleanup."""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from app.celerytools.utils import QueueOnce

logger = get_task_logger(__name__)


KEY_TTL_DAYS = 7
AUTO_RENEW_IDLE_DAYS = 7  # auto-renew keys are only deleted after this many days of non-use


@shared_task(base=QueueOnce, once={"graceful": True})
def delete_expired_api_keys():
    """Delete expired keys. Auto-renew keys only expire after idle period (no use)."""
    from app.killtracker.models import ApiKey
    now = timezone.now()

    # Non-renewing keys: delete if older than TTL from creation
    expired = ApiKey.objects.filter(
        is_permanent=False, auto_renew=False,
        created_at__lt=now - timedelta(days=KEY_TTL_DAYS),
    )
    n1, _ = expired.delete()

    # Auto-renew keys: delete only if never used OR idle past threshold
    from django.db.models import Q
    idle_cutoff = now - timedelta(days=AUTO_RENEW_IDLE_DAYS)
    idle = ApiKey.objects.filter(is_permanent=False, auto_renew=True).filter(
        Q(last_used_at__isnull=True, created_at__lt=idle_cutoff) |
        Q(last_used_at__lt=idle_cutoff)
    )
    n2, _ = idle.delete()

    total = n1 + n2
    if total:
        logger.info("killtracker: deleted %d expired API keys (%d fixed, %d idle-renew)", total, n1, n2)
    return total


# The client may legitimately post buffered kills up to the offline grace window late, but a
# timestamp older than that (or in the future) is either clock skew or fabrication — clamp it.
MAX_KILL_AGE = timedelta(days=7)
MAX_KILL_FUTURE_SKEW = timedelta(minutes=10)


def _parse_time(val):
    if not val:
        return None
    if isinstance(val, datetime):
        parsed = val
    else:
        try:
            parsed = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    now = timezone.now()
    if parsed > now + MAX_KILL_FUTURE_SKEW or parsed < now - MAX_KILL_AGE:
        logger.info("killtracker: client-supplied kill time %s outside accepted window, using now()", parsed)
        return now
    return parsed


@shared_task
def process_kill_report(api_key_id: int, killer_user_id: int | None, payload: dict):
    """Scrape RSI for victim details, create KillEvent (→ signal → pub/sub)."""
    from app.killtracker.models import ApiKey, KillEvent
    from app.killtracker.rsi_scraper import fetch_rsi_profile

    victim = payload.get("victim") or ""
    profile = fetch_rsi_profile(victim) if victim else {}

    try:
        KillEvent.objects.create(
            killer_id=killer_user_id,
            killer_name=payload.get("player", ""),
            victim=victim,
            game_mode=payload.get("game_mode", "SC_Default") or "SC_Default",
            weapon=payload.get("weapon", "") or "",
            zone=payload.get("zone", "") or "",
            killers_ship=payload.get("killers_ship", "") or "",
            client_ver=payload.get("client_ver", "") or "",
            time=_parse_time(payload.get("time")) or timezone.now(),
            victim_org=profile.get("org_name", "") or "",
            victim_org_sid=profile.get("org_short", "") or "",
            victim_avatar=profile.get("avatar_url", "") or "",
            victim_enlisted=profile.get("enlisted_date", "") or "",
            victim_location=profile.get("location", "") or "",
            anonymous=bool(payload.get("anonymous")),
            incap=bool(payload.get("incap")),
            crime_type=(payload.get("crime_type") or "")[:64],
            api_key_id=api_key_id,
        )
    except Exception as e:
        # unique constraint → duplicate; swallow
        logger.info("killtracker: duplicate or create failure: %s", e)
        return "dupe"

    ApiKey.objects.filter(pk=api_key_id).update(last_used_at=timezone.now())
    return "ok"


@shared_task(base=QueueOnce, once={"graceful": True})
def purge_stale_device_auth_requests():
    """Delete device-login rows a day past expiry. Nothing reads them after the token is
    delivered (or the code expires), so without this every login attempt ever made stays in
    the table forever."""
    from app.killtracker.models import DeviceAuthRequest
    cutoff = timezone.now() - timedelta(days=1)
    deleted, _ = DeviceAuthRequest.objects.filter(expires_at__lt=cutoff).delete()
    if deleted:
        logger.info("killtracker: purged %d stale device auth requests", deleted)
    return deleted


@shared_task(base=QueueOnce, once={"graceful": True})
def prune_stale_rsi_snapshots():
    """Delete RSIProfileSnapshot rows not updated in KTRSISnapshotStaleDays days."""
    from app.killtracker.models import RSIProfileSnapshot
    from app.killtracker.preferences import KTRSISnapshotStaleDays
    from app.preferences.utils import get_global_preference

    stale_days = get_global_preference(KTRSISnapshotStaleDays.import_path)
    if not stale_days:
        return 0

    cutoff = timezone.now() - timedelta(days=stale_days)
    deleted, _ = RSIProfileSnapshot.objects.filter(last_checked_at__lt=cutoff).delete()
    if deleted:
        logger.info("killtracker: pruned %d stale RSIProfileSnapshot rows", deleted)
    return deleted


# Periodic schedule registration
try:
    from app.custom_celery_beat.utils import register_periodic_task
    register_periodic_task(
        name="killtracker.delete_expired_api_keys",
        task="app.killtracker.tasks.delete_expired_api_keys",
        crontab={"minute": "*/10"},
    )
    register_periodic_task(
        name="killtracker.purge_stale_device_auth_requests",
        task="app.killtracker.tasks.purge_stale_device_auth_requests",
        crontab={"hour": "*/6", "minute": "30"},
    )
    register_periodic_task(
        name="killtracker.prune_stale_rsi_snapshots",
        task="app.killtracker.tasks.prune_stale_rsi_snapshots",
        crontab={"hour": "3", "minute": "0"},
    )
except Exception:
    logger.debug("killtracker: register_periodic_task unavailable (ok during migrate)")
