import logging
from datetime import datetime, timezone

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from app.sc_tracker.preferences import sc_prefs

logger = logging.getLogger(__name__)

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


def _calc_hangar() -> dict:
    open_ms  = sc_prefs.hangar_open_ms
    close_ms = sc_prefs.hangar_close_ms
    t0_ms    = sc_prefs.hangar_t0_ms
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
        "patch_info":          sc_prefs.hangar_patch_info,
        "synced_at":           datetime.now(timezone.utc).isoformat(),
        "sync_source":         "env",
        "can_insert_compboards": phase == "GREEN",
    }


class SCTrackerView(LoginRequiredMixin, TemplateView):
    template_name = "sc_tracker/dashboard.html"


class RSIStatusProxyView(View):
    """Returns the latest RSI status JSON from the DB cache (RSIStatusSnapshot)."""

    def get(self, request, *args, **kwargs):
        from app.sc_tracker.models import RSIStatusSnapshot

        snap = RSIStatusSnapshot.objects.filter(id=1).first()
        if not snap:
            return JsonResponse(
                {"error": "Status data not yet available — Celery task may not have run yet."},
                status=503,
            )
        return JsonResponse(snap.raw_json, safe=False)


class RecentActivityAPIView(View):
    """Returns the 10 most recently resolved RSI issues from our DB."""

    def get(self, request, *args, **kwargs):
        from app.sc_tracker.models import RSIIssue
        issues = list(
            RSIIssue.objects.filter(resolved=True)
            .order_by("-rsi_resolved_at", "-updated_at")
            .values("title", "affected", "severity", "permalink", "rsi_resolved_at", "first_seen")[:10]
        )
        def _clean(p):
            if not p:
                return ""
            base = p.replace("index.html", "").replace("index.xml", "").rstrip("/")
            return f"{base}/index.html" if base else ""

        data = []
        for i in issues:
            data.append({
                "title":       i["title"],
                "affected":    i["affected"],
                "severity":    i["severity"],
                "permalink":   _clean(i["permalink"]),
                "resolved_at": i["rsi_resolved_at"].isoformat() if i["rsi_resolved_at"] else None,
                "created_at":  i["first_seen"].isoformat() if i["first_seen"] else None,
            })
        return JsonResponse(data, safe=False)


class HangarTimerAPIView(View):
    def get(self, request, *args, **kwargs):
        return JsonResponse(_calc_hangar())


class HangarSyncDebugView(View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return JsonResponse({"error": "Staff only"}, status=403)

        state     = _calc_hangar()
        remaining = state["time_remaining"]
        h, m, s   = remaining // 3600, (remaining % 3600) // 60, remaining % 60

        return JsonResponse(
            {
                "constants": {
                    "SC_HANGAR_T0_MS":    sc_prefs.hangar_t0_ms,
                    "SC_HANGAR_OPEN_MS":  sc_prefs.hangar_open_ms,
                    "SC_HANGAR_CLOSE_MS": sc_prefs.hangar_close_ms,
                },
                "computed": {
                    "phase":          state["phase"],
                    "time_remaining": f"{h}:{m:02d}:{s:02d}",
                    "cycle_pct":      state["cycle_percent"],
                },
                "patch_info": sc_prefs.hangar_patch_info,
                "update_instructions": (
                    "Update SC_HANGAR_T0_MS, SC_HANGAR_OPEN_MS, SC_HANGAR_CLOSE_MS "
                    "in your .env file after each SC patch, then restart."
                ),
            },
            json_dumps_params={"indent": 2},
        )