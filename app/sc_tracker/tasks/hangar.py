import json
import logging
import re
from datetime import datetime, timezone

import requests
from celery import shared_task
from celery.utils.log import get_task_logger
from django_celery_beat.models import IntervalSchedule

from app.celerytools.utils import register_periodic_task
from app.preferences.utils import get_global_preference
from app.sc_tracker import preferences as sc_prefs_module

logger = get_task_logger(__name__)

PUBSUB_CHANNEL = "sc_tracker:hangar_events"
WEBSITE_URL    = "https://exec.xyxyll.com/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

LIGHT_THRESHOLDS = [
    {"min": 0,             "max": 12*60*1000,  "lights": 5},
    {"min": 12*60*1000,    "max": 24*60*1000,  "lights": 4},
    {"min": 24*60*1000,    "max": 36*60*1000,  "lights": 3},
    {"min": 36*60*1000,    "max": 48*60*1000,  "lights": 2},
    {"min": 48*60*1000,    "max": 60*60*1000,  "lights": 1},
    {"min": 60*60*1000,    "max": 65*60*1000,  "lights": 0},
    {"min": 65*60*1000,    "max": 89*60*1000,  "lights": 0},
    {"min": 89*60*1000,    "max": 113*60*1000, "lights": 1},
    {"min": 113*60*1000,   "max": 137*60*1000, "lights": 2},
    {"min": 137*60*1000,   "max": 161*60*1000, "lights": 3},
    {"min": 161*60*1000,   "max": 185*60*1000, "lights": 4},
]


def _get_pref(cls):
    return get_global_preference(cls.import_path)


def _publish(payload: dict):
    try:
        from django.core.cache import caches
        redis_client = caches["default"].client.get_client()
        redis_client.publish(PUBSUB_CHANNEL, json.dumps(payload))
    except Exception as exc:
        logger.warning("[HANGAR] Redis publish failed: %s", exc)


def _calc_state() -> dict:
    open_ms  = _get_pref(sc_prefs_module.HangarOpenMs)
    close_ms = _get_pref(sc_prefs_module.HangarCloseMs)
    t0_ms    = _get_pref(sc_prefs_module.HangarT0Ms)
    cycle_ms = open_ms + close_ms

    now_ms        = datetime.now(timezone.utc).timestamp() * 1000
    time_in_cycle = (now_ms - t0_ms) % cycle_ms

    if time_in_cycle < open_ms:
        phase     = "GREEN"
        remaining = open_ms - time_in_cycle
    else:
        phase     = "RED"
        remaining = close_ms - (time_in_cycle - open_ms)

    threshold = next(
        (t for t in LIGHT_THRESHOLDS if t["min"] <= time_in_cycle < t["max"]),
        {"lights": 0},
    )

    return {
        "phase":               phase,
        "time_remaining":      int(remaining / 1000),
        "active_lights":       threshold["lights"],
        "total_lights":        5,
        "cycle_percent":       round((time_in_cycle / cycle_ms) * 100, 1),
        "open_duration_secs":  int(open_ms / 1000),
        "close_duration_secs": int(close_ms / 1000),
        "patch_info":          _get_pref(sc_prefs_module.HangarPatchInfo),
        "can_insert_compboards": phase == "GREEN",
    }


@register_periodic_task(every=30, interval=IntervalSchedule.SECONDS, name="sc-tracker: Update Hangar Embed")
@shared_task(name="app.sc_tracker.tasks.hangar.update_hangar_embed")
def update_hangar_embed():
    """Pure math — calculates hangar state and publishes to Redis for the bot cog."""
    if not _get_pref(sc_prefs_module.HangarEnabled):
        return
    state = _calc_state()
    _publish({"type": "update_embed", "state": state})
    logger.debug("[HANGAR] Embed update published — phase=%s remaining=%ds", state["phase"], state["time_remaining"])


@register_periodic_task(every=15, interval=IntervalSchedule.MINUTES, name="sc-tracker: Sync Hangar from Website")
@shared_task(name="app.sc_tracker.tasks.hangar.sync_hangar_from_website", bind=True, max_retries=3)
def sync_hangar_from_website(self):
    """Scrapes exec.xyxyll.com/app.js for updated timing constants and warns if they changed."""
    if not _get_pref(sc_prefs_module.HangarEnabled):
        return

    try:
        resp = requests.get(WEBSITE_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text

        import urllib.parse
        app_js_match = re.search(r'src="([^"]*app\.js[^"]*)"', html, re.IGNORECASE)
        if not app_js_match:
            logger.warning("[HANGAR] Could not find app.js on %s", WEBSITE_URL)
            return

        js_url = urllib.parse.urljoin(WEBSITE_URL, app_js_match.group(1))
        js_resp = requests.get(js_url, headers=_HEADERS, timeout=15)
        js_resp.raise_for_status()
        js = js_resp.text

    except Exception as exc:
        logger.error("[HANGAR] Website sync failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)

    from app.sc_tracker.models import SCTrackerConfig

    cfg      = SCTrackerConfig.get()
    findings = {}
    updates  = {}

    t0_match = re.search(r"INITIAL_OPEN_TIME\s*=\s*new\s+Date\([\"']([^\"']+)[\"']\)", js)
    if t0_match:
        try:
            t0_ms = int(datetime.fromisoformat(t0_match.group(1)).timestamp() * 1000)
            findings["HangarT0Ms"] = t0_ms
            if t0_ms != cfg.hangar_t0_ms:
                logger.warning("[HANGAR] ⚠️  T0 changed: %d → %d. Auto-applying.", cfg.hangar_t0_ms, t0_ms)
                updates["hangar_t0_ms"] = t0_ms
        except Exception:
            pass

    open_match = re.search(r"OPEN_DURATION\s*=\s*(\d+)", js)
    if open_match:
        val = int(open_match.group(1))
        findings["HangarOpenMs"] = val
        if val != cfg.hangar_open_ms:
            logger.warning("[HANGAR] ⚠️  OPEN_DURATION changed: %d → %d. Auto-applying.", cfg.hangar_open_ms, val)
            updates["hangar_open_ms"] = val

    close_match = re.search(r"CLOSE_DURATION\s*=\s*(\d+)", js)
    if close_match:
        val = int(close_match.group(1))
        findings["HangarCloseMs"] = val
        if val != cfg.hangar_close_ms:
            logger.warning("[HANGAR] ⚠️  CLOSE_DURATION changed: %d → %d. Auto-applying.", cfg.hangar_close_ms, val)
            updates["hangar_close_ms"] = val

    patch_match = re.search(r"Updated\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}\s+for\s+Star\s+Citizen\s+Patch\s+[^<]+", html)
    if patch_match:
        patch_info = patch_match.group(0).strip()
        findings["HangarPatchInfo"] = patch_info
        if patch_info != cfg.hangar_patch_info:
            updates["hangar_patch_info"] = patch_info

    if updates:
        SCTrackerConfig.objects.filter(id=1).update(**updates)
        logger.info("[HANGAR] Config updated from website: %s", updates)

    _publish({"type": "website_sync_complete", "findings": findings, "applied": list(updates.keys())})
    logger.info("[HANGAR] Website sync complete: %s", findings)