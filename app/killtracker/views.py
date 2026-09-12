import hmac
import json
import logging
import re
import secrets
from datetime import timedelta  # noqa: F401

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, F, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from django_ratelimit.decorators import ratelimit  # SEC-04

from app.killtracker.game_data import (
    is_ignored_victim,
    preprocess_name,
)
from app.killtracker.models import (
    ApiKey,
    BlacklistEntry,
    ClientRelease,
    DeviceAuthRequest,
    KillEvent,
    ParserRelease,
    PlayerAlias,
    _gen_key,
    hash_key,
)
from app.preferences.utils import get_global_preference as _gp

logger = logging.getLogger(__name__)


def _parse_anon(val) -> bool:
    """Accept either a plain bool (new client) or {"enabled": bool} dict (old client)."""
    if isinstance(val, bool):
        return val
    if isinstance(val, dict):
        return bool(val.get("enabled"))
    return False


# ── Ingestion API ─────────────────────────────────────────────────────────────

# SEC-05: every KillTracker desktop-client endpoint requires this header, set to a value baked
# into the desktop client (modules/api_client.py CLIENT_SECRET). It is a genuine secret, not a
# public string like a User-Agent — it's checked with a constant-time comparison so the server
# can't be used as a timing oracle to brute-force it. This stops casual scripting/probing by
# people who haven't pulled apart the client binary; it does NOT stop a determined reverse
# engineer who extracts the value (no client-embedded secret can survive that). Real identity and
# authorization still come from the per-user ApiKey / use_tracker perm checked further down in
# each view — this header is defense-in-depth, not a substitute for that.
KILLTRACKER_CLIENT_SECRET_HEADER = "X-BV-Client-Secret"


_warned_no_client_secret = False


def _require_killtracker_client(request) -> JsonResponse | None:
    """Return a 403 JsonResponse if this request didn't present the KillTracker client secret,
    or None to let the view continue. If KILLTRACKER_CLIENT_SECRET isn't configured server-side,
    the gate is a no-op (logged once) so an un-configured environment still works."""
    global _warned_no_client_secret
    expected = settings.KILLTRACKER_CLIENT_SECRET
    if not expected:
        if not _warned_no_client_secret:
            _warned_no_client_secret = True
            logger.warning("KILLTRACKER_CLIENT_SECRET is not set; client-secret gate is disabled.")
        return None
    provided = request.headers.get(KILLTRACKER_CLIENT_SECRET_HEADER, "")
    if not provided or not hmac.compare_digest(provided, expected):
        return JsonResponse({"status": "error", "reason": "client not recognized"}, status=403)
    return None


# RSI handles are 1-64 chars of letters/digits/dot/dash/underscore. Anything else in a
# player/victim field is either garbage or an attempt to smuggle path/query segments into the
# RSI scrape URL — reject it before it reaches Celery or the DB.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _valid_handle(name: str) -> bool:
    return bool(_HANDLE_RE.match(name or ""))


def _blacklisted_for_key(key, player: str | None = None) -> bool:
    """A key is blocked if the reported handle OR any handle aliased to the key's owner is
    blacklisted. Checking the owner's aliases means a blacklisted player can't dodge the ban
    by just self-reporting a different name."""
    names = set()
    if player:
        names.add(player.lower())
    if key.user_id:
        names.update(
            n.lower() for n in
            PlayerAlias.objects.filter(user_id=key.user_id).values_list("game_name", flat=True)
        )
    if not names:
        return False
    q = Q()
    for n in names:
        q |= Q(game_name__iexact=n)
    return BlacklistEntry.objects.filter(q).exists()


def _resolve_api_key(request, body: dict):
    """Client sends key via Authorization header; legacy payloads used body.api_key / body.report_body."""
    api_key = (
        request.headers.get("Authorization", "").strip()
        or body.get("api_key")
        or request.headers.get("X-API-Key", "")
    )
    # Kill payload is either flat (new client) or under report_body (legacy)
    report = body.get("report_body") or body
    return api_key, report


# SEC-04: throttle by source IP. block=True -> 429/403 via Ratelimited when exceeded.
# Tune rates to your real client cadence; these are conservative starting points.
@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(ratelimit(key="ip", rate="120/m", method="POST", block=True), name="post")
class ReportKillAPIView(View):
    """POST /killtracker/reportKill — consumed by the KillTracker desktop client."""

    def post(self, request):
        denied = _require_killtracker_client(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return JsonResponse({"status": "error", "reason": "invalid json"}, status=400)

        api_key, report = _resolve_api_key(request, body)

        if not api_key:
            return JsonResponse({"status": "error", "reason": "missing api_key"}, status=401)

        try:
            key = ApiKey.objects.select_related("user").get(key_hash=hash_key(api_key), revoked=False)
        except ApiKey.DoesNotExist:
            return JsonResponse({"status": "error", "reason": "invalid key"}, status=403)

        # Gate: the key owner must currently hold killtracker.use_tracker (granted via the
        # Discord-role -> Django-group sync). Checked live on every ingest so revoking the
        # role/perm kills the client on its next POST. SET_NULL keys (no user) fail closed.
        if not key.user or not key.user.has_perm("killtracker.use_tracker"):
            return JsonResponse({"status": "forbidden", "reason": "tracker access revoked"}, status=403)

        player = (report.get("player") or "").strip()
        victim = (report.get("victim") or "").strip()
        client_ver = (report.get("client_ver") or "")[:16]

        if not player or not victim:
            return JsonResponse({"status": "error", "reason": "missing player/victim"}, status=400)

        from app.killtracker.preferences import KTRequiredClientVersion
        required_prefix = _gp(KTRequiredClientVersion.import_path) or "1.6"
        if not client_ver.startswith(required_prefix):
            return JsonResponse({"status": "ignore", "reason": "client_ver"}, status=200)

        # The killer must be a real, well-formed RSI handle; a malformed one can't be scraped
        # or attributed and only exists to pollute the leaderboard. Victims are checked more
        # loosely below (NPC names legitimately contain other characters, and is_ignored_victim
        # drops those) but still bounded.
        if not _valid_handle(player):
            return JsonResponse({"status": "error", "reason": "invalid player handle"}, status=400)
        if len(victim) > 64:
            return JsonResponse({"status": "error", "reason": "victim too long"}, status=400)

        if player.lower() == victim.lower():
            return JsonResponse({"status": "ignore", "reason": "self-kill"}, status=200)

        if is_ignored_victim(victim):
            return JsonResponse({"status": "ignore", "reason": "npc"}, status=200)

        normalized = preprocess_name(player)
        if _blacklisted_for_key(key, player):
            return JsonResponse({"status": "ignore", "reason": "blacklisted"}, status=200)

        # Resolve killer → OrgPlayer via PlayerAlias (authoritative) else key owner.
        # Anti-impersonation: if the reported handle is aliased to a DIFFERENT user, this key
        # may not report kills under it — otherwise any key holder could farm kills onto (or
        # frame) another member's account just by typing their handle. Exception: reportACKill
        # is the VICTIM's client vouching that someone else killed them (Arena Commander death
        # reports), so there the killer being another member is the whole point.
        is_ac_report = bool(request.resolver_match) and request.resolver_match.url_name == "report_ac_kill"
        killer_user_id = key.user_id
        alias = PlayerAlias.objects.filter(game_name__iexact=player).select_related("user").first()
        if alias:
            if alias.user_id != key.user_id and not is_ac_report:
                return JsonResponse(
                    {"status": "error", "reason": "player handle belongs to another member"},
                    status=403,
                )
            killer_user_id = alias.user_id

        # Crime type correlated by the client from the game's "Crime Committed" HUD
        # notification (slug form, e.g. "homicide"). Strictly sanitized: it feeds the crime
        # leaderboards directly. Clients that predate crime detection simply don't send it.
        crime_type = (report.get("crime_type") or "").strip().lower()[:64]
        if not all(c.isalnum() or c == "_" for c in crime_type):
            crime_type = ""

        # Hand off to Celery for RSI scrape + persist
        from app.killtracker.tasks import process_kill_report
        process_kill_report.delay(
            api_key_id=str(key.id),
            killer_user_id=str(killer_user_id),
            payload={
                "player": player,
                "victim": victim,
                # Truncate to the KillEvent column widths so an oversized field degrades
                # instead of blowing up the Celery task on the DB insert.
                "game_mode": (report.get("game_mode") or "SC_Default")[:64],
                "weapon": (report.get("weapon") or "")[:128],
                "zone": (report.get("zone") or "")[:128],
                "killers_ship": (report.get("killers_ship") or "")[:128],
                "client_ver": client_ver,
                "time": report.get("time"),
                "anonymous": _parse_anon(report.get("anonymize_state")),
                "incap": bool(report.get("incap")),
                "crime_type": crime_type,
            },
        )
        return JsonResponse({"status": "queued"}, status=202)


# ── Web UI ────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(ratelimit(key="ip", rate="30/m", method="POST", block=True), name="post")  # SEC-04
class ValidateKeyAPIView(View):
    """POST /killtracker/validateKey — returns {expires_at} for the key."""

    def post(self, request):
        denied = _require_killtracker_client(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        api_key = (
            request.headers.get("Authorization", "").strip()
            or body.get("api_key")
        )
        if not api_key:
            return JsonResponse({"status": "error"}, status=401)
        try:
            key = ApiKey.objects.select_related("user").get(key_hash=hash_key(api_key), revoked=False)
        except ApiKey.DoesNotExist:
            return JsonResponse({"status": "invalidated"}, status=403)

        # Same gate as reportKill: lose the Discord role -> lose the perm -> client is told its
        # access is invalidated and stops on its next heartbeat.
        if not key.user or not key.user.has_perm("killtracker.use_tracker"):
            return JsonResponse({"status": "invalidated", "reason": "tracker access revoked"}, status=403)

        player_name = body.get("player_name", "").strip()
        if player_name == "N/A" or not _valid_handle(player_name):
            player_name = ""

        # Blacklist check: matches the reported handle AND every handle aliased to the key's
        # owner, so a blacklisted player can't slip past by reporting a different (or no) name.
        if _blacklisted_for_key(key, player_name):
            return JsonResponse({"status": "blacklisted", "reason": "player is blacklisted"}, status=403)

        if player_name and not key.label.startswith(player_name):
            # Replace any previous "handle – " prefix instead of stacking a new one in front of
            # it — otherwise a client sending a new name each heartbeat grows the label forever.
            base_label = key.label.rsplit(" – ", 1)[-1]
            ApiKey.objects.filter(pk=key.id).update(
                last_used_at=timezone.now(),
                label=f"{player_name} – {base_label}"[:64],
            )
        else:
            ApiKey.objects.filter(pk=key.id).update(last_used_at=timezone.now())
        # Permanent → far future. Auto-renew → rolling 7d from now. Else → 7d from creation.
        if key.is_permanent:
            expires = timezone.now() + timedelta(days=3650)
        elif key.auto_renew:
            expires = timezone.now() + timedelta(days=7)
        else:
            expires = key.created_at + timedelta(days=7)
        return JsonResponse({
            "status": "ok",
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        })


@method_decorator(ratelimit(key="ip", rate="120/m", method="GET", block=True), name="get")  # SEC-04
class ServerDataMapView(View):
    """GET /killtracker/api/server/data/<type>/ — weapons / ships / ignoredVictimRules / shipPrefixes
    / exclusionRules / parser. All shapes here are part of the client contract (modules/api_client.py
    + modules/log_parser.py) — keep field names in sync with what the client reads."""

    def get(self, request, data_type: str):
        from app.killtracker.game_data import get_game_data
        denied = _require_killtracker_client(request)
        if denied:
            return denied
        api_key = request.headers.get("Authorization", "").strip()
        if not api_key or not ApiKey.objects.filter(key_hash=hash_key(api_key), revoked=False).exists():
            return JsonResponse({"status": "error"}, status=403)

        gd = get_game_data()
        if data_type == "weapons":
            payload = [{"id": k, "name": v} for k, v in gd["weapons"].items()]
        elif data_type == "ships":
            payload = [{"id": k, "name": v} for k, v in gd["ships"].items()]
        elif data_type == "ignoredVictimRules":
            payload = [{"value": s} for s in gd["ignored_victim_substrings"]] + \
                      [{"value": p} for p in gd["ignored_victim_prefixes"]]
        elif data_type == "shipPrefixes":
            payload = list(gd["ship_manufacturer_prefixes"])
        elif data_type == "exclusionRules":
            payload = gd["exclusion_rules"]
        elif data_type == "partMap":
            payload = {str(k): v for k, v in gd["part_map"].items()}
        elif data_type == "processNames":
            payload = gd["process_names"]
        elif data_type == "freeFlightGameModes":
            payload = list(gd["free_flight_game_modes"])
        elif data_type == "contractPatterns":
            payload = gd["contract_patterns"]
        elif data_type == "rsiSelectors":
            payload = gd["rsi_selectors"]
        elif data_type == "userCfgLines":
            payload = list(gd["user_cfg_lines"])
        elif data_type == "parser":
            release = ParserRelease.objects.filter(is_active=True).order_by("-created_at").first()
            if release is None:
                return JsonResponse({"status": "no parser published"}, status=404)
            payload = {"version": release.version, "source": release.source, "signature": release.signature}
        else:
            return JsonResponse({"status": "unknown type"}, status=400)
        return JsonResponse({data_type: payload})


@method_decorator(ratelimit(key="ip", rate="120/m", method="GET", block=True), name="get")  # SEC-04
class ServerDataAllView(View):
    """GET /killtracker/api/server/data/all — returns every data map the client needs in one
    request (weapons, ignoredVictimRules, shipPrefixes, exclusionRules, parser, userCfgLines).
    Replaces the individual /data/<type> calls the client used to make sequentially."""

    def get(self, request):
        from app.killtracker.game_data import get_game_data
        from app.killtracker.preferences import KTClientDownloadURL, KTLatestClientVersion
        denied = _require_killtracker_client(request)
        if denied:
            return denied
        api_key = request.headers.get("Authorization", "").strip()
        if not api_key or not ApiKey.objects.filter(key_hash=hash_key(api_key), revoked=False).exists():
            return JsonResponse({"status": "error"}, status=403)

        release = ParserRelease.objects.filter(is_active=True).order_by("-created_at").first()
        parser_payload = (
            {"version": release.version, "source": release.source, "signature": release.signature}
            if release else None
        )
        gd = get_game_data()

        # Client update channel: the client repo is private, so the desktop app can't ask
        # GitHub for releases — Servitor is the single source of truth for "is there a newer
        # client". An uploaded ClientRelease (admin: Client releases) is authoritative and
        # enables in-app auto-update; the KTLatestClientVersion/KTClientDownloadURL preferences
        # remain as a notice-only fallback when no exe has been uploaded.
        client_release = ClientRelease.objects.filter(is_active=True).order_by("-created_at").first()
        if client_release and client_release.file:
            dl = request.build_absolute_uri(reverse("killtracker:client_download"))
            client_info = {
                "latest_version": client_release.version,
                "download_url": dl,
                "exe_url": dl,
                # Per-variant SHA-256 so the client can verify its download's integrity.
                "exe_sha256": client_release.sha256 or "",
                "exe_sha256_nosound": client_release.sha256_nosound or client_release.sha256 or "",
            }
        else:
            client_info = {
                "latest_version": _gp(KTLatestClientVersion.import_path) or "",
                "download_url": _gp(KTClientDownloadURL.import_path) or "",
                "exe_url": "",
                "exe_sha256": "",
                "exe_sha256_nosound": "",
            }

        return JsonResponse({
            "client": client_info,
            "weapons": [{"id": k, "name": v} for k, v in gd["weapons"].items()],
            "ships": [{"id": k, "name": v} for k, v in gd["ships"].items()],
            "ignoredVictimRules": [{"value": s} for s in gd["ignored_victim_substrings"]] +
                                  [{"value": p} for p in gd["ignored_victim_prefixes"]],
            "shipPrefixes": list(gd["ship_manufacturer_prefixes"]),
            "exclusionRules": gd["exclusion_rules"],
            "parser": parser_payload,
            "userCfgLines": list(gd["user_cfg_lines"]),
            "partMap": {str(k): v for k, v in gd["part_map"].items()},
            "processNames": gd["process_names"],
            "freeFlightGameModes": list(gd["free_flight_game_modes"]),
            "contractPatterns": gd["contract_patterns"],
            "rsiSelectors": gd["rsi_selectors"],
        })


@method_decorator(ratelimit(key="ip", rate="10/m", method="GET", block=True), name="get")  # SEC-04
class ClientDownloadView(View):
    """GET /killtracker/download/latest — the active client exe.

    Two legitimate callers, two auth paths:
      * the desktop app's auto-updater: client-secret header + a valid API key;
      * a logged-in member with tracker access clicking Download on their dashboard.
    Anyone else gets 403 — the exe is org-members-only, same as everything else here."""

    def get(self, request):
        from django.http import FileResponse

        authorized = False
        if request.user.is_authenticated and request.user.has_perm("killtracker.use_tracker"):
            authorized = True
        else:
            if _require_killtracker_client(request) is None:
                api_key = request.headers.get("Authorization", "").strip()
                if api_key and ApiKey.objects.filter(key_hash=hash_key(api_key), revoked=False).exists():
                    authorized = True
        if not authorized:
            return JsonResponse({"status": "error", "reason": "not authorized"}, status=403)

        release = ClientRelease.objects.filter(is_active=True).order_by("-created_at").first()
        if release is None or not release.file:
            return JsonResponse({"status": "error", "reason": "no release published"}, status=404)

        # Serve the build matching the caller's variant so a no-sounds client updates to the
        # no-sounds exe (and vice-versa). Falls back to the with-sounds file if the no-sounds
        # one wasn't uploaded.
        variant = request.GET.get("variant", "")
        if variant == "nosound" and release.file_nosound:
            f, fname = release.file_nosound, "BlightVeil KillTracker (no sounds).exe"
        else:
            f, fname = release.file, "BlightVeil KillTracker.exe"
        resp = FileResponse(f.open("rb"), as_attachment=True, filename=fname)
        resp["X-KT-Version"] = release.version
        return resp


@method_decorator(ratelimit(key="ip", rate="60/m", method="GET", block=True), name="get")  # SEC-04
class RSIProfileView(View):
    """GET /killtracker/api/rsi/<handle> — RSI citizen profile, scraped server-side.

    Lets the desktop client stay dumb: instead of every client scraping
    robertsspaceindustries.com itself (needing CSS selectors pushed to it), it asks Servitor,
    which scrapes once and caches. Same auth gates as the other client endpoints."""

    CACHE_TTL = 6 * 3600  # RSI profile data changes rarely; don't hammer their site

    def get(self, request, handle: str):
        from django.core.cache import caches

        denied = _require_killtracker_client(request)
        if denied:
            return denied
        api_key = request.headers.get("Authorization", "").strip()
        if not api_key or not ApiKey.objects.filter(key_hash=hash_key(api_key), revoked=False).exists():
            return JsonResponse({"status": "error"}, status=403)
        if not _valid_handle(handle):
            return JsonResponse({"status": "error", "reason": "invalid handle"}, status=400)

        cache = caches["default"]
        cache_key = f"killtracker.rsi_profile.{handle.lower()}"
        profile = cache.get(cache_key)
        if profile is None:
            from app.killtracker.rsi_scraper import fetch_rsi_profile
            profile = fetch_rsi_profile(handle)
            cache.set(cache_key, profile, self.CACHE_TTL)
        return JsonResponse({"profile": profile})


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "killtracker/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["keys"] = ApiKey.objects.filter(user=self.request.user, revoked=False).order_by("-created_at")
        ctx["aliases"] = PlayerAlias.objects.filter(user=self.request.user)
        ctx["recent_kills"] = KillEvent.objects.filter(killer=self.request.user).order_by("-time")[:25]
        release = ClientRelease.objects.filter(is_active=True).order_by("-created_at").first()
        ctx["client_release"] = release
        ctx["client_has_nosound"] = bool(release and release.file_nosound)
        return ctx


@login_required
def generate_key_web(request):
    # Manual key generation has been REMOVED. Keys are issued automatically via Discord device
    # login (the device flow mints and labels them per machine). This no-op stub is kept only so
    # any lingering reverse('killtracker:generate_key') resolves; it never mints a key.
    return redirect(reverse("killtracker:dashboard"))


@login_required
def revoke_key_web(request, key_id: int):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    ApiKey.objects.filter(pk=key_id, user=request.user).update(revoked=True)
    return redirect(reverse("killtracker:dashboard"))


@login_required
def toggle_auto_renew(request, key_id: int):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    key = ApiKey.objects.filter(pk=key_id, user=request.user).first()
    if key:
        key.auto_renew = not key.auto_renew
        key.save(update_fields=["auto_renew"])
    return redirect(reverse("killtracker:dashboard"))


class LeaderboardView(TemplateView):
    template_name = "killtracker/leaderboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["app_embedded"] = self.request.GET.get("app") == "1"
        return ctx


class LeaderboardDataView(View):
    """JSON feed for the leaderboard — all panels in one request."""

    WINDOWS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}

    def get(self, request):
        from django.db.models import Case, IntegerField, Sum, Value, When
        from app.killtracker.game_data import CRIME_TYPES, CRIME_POINTS, CRIME_COLORS, CRIME_ICONS

        # Incaps are recorded (KillEvent.incap=True) but are not kills — every panel here is a
        # kill leaderboard, so they're excluded from the base queryset and only surfaced as a
        # separate total.
        incap_qs = KillEvent.objects.filter(incap=True)
        qs = KillEvent.objects.filter(incap=False)
        window = request.GET.get("window", "30d")
        if window in self.WINDOWS:
            cutoff = timezone.now() - timedelta(days=self.WINDOWS[window])
            qs = qs.filter(time__gte=cutoff)
            incap_qs = incap_qs.filter(time__gte=cutoff)
        mode = request.GET.get("mode")
        if mode:
            qs = qs.filter(game_mode=mode)
            incap_qs = incap_qs.filter(game_mode=mode)

        total_kills = qs.count()
        crime_label_map = dict(CRIME_TYPES)

        # Anonymity: any panel that surfaces a killer's name must be built from kills that
        # were NOT reported as anonymous — otherwise the rankings/points/top-killer panels
        # would leak the very name the global feed redacts.
        named_qs = qs.exclude(anonymous=True)

        # ── Crime rankings: top 5 per crime type ─────────────────────────────
        crime_rankings = {}
        for key, label in CRIME_TYPES:
            rows = list(
                named_qs.filter(crime_type=key)
                  .values("killer_name")
                  .annotate(count=Count("id"))
                  .order_by("-count")[:5]
            )
            crime_rankings[key] = {
                "label": label,
                "color": CRIME_COLORS.get(key, "#8a96ab"),
                "icon":  CRIME_ICONS.get(key, "⚡"),
                "rows":  rows,
            }

        # ── Crime points overall ranking ──────────────────────────────────────
        whens = [When(crime_type=k, then=Value(v)) for k, v in CRIME_POINTS.items()]
        crime_points_qs = list(
            named_qs.annotate(pts=Case(*whens, default=Value(0), output_field=IntegerField()))
              .values("killer_name")
              .annotate(total_points=Sum("pts"), total_kills=Count("id"))
              .order_by("-total_points")[:25]
        )

        # ── Global feed: last 100 kills ───────────────────────────────────────
        global_feed = []
        for ev in qs.order_by("-time")[:100]:
            global_feed.append({
                "killer":      ev.killer_name if not ev.anonymous else "REDACTED",
                "victim":      ev.victim,
                "crime_type":  ev.crime_type,
                "crime_label": crime_label_map.get(ev.crime_type, "Kill") if ev.crime_type else "Kill",
                "crime_color": CRIME_COLORS.get(ev.crime_type, "#8a96ab"),
                "crime_icon":  CRIME_ICONS.get(ev.crime_type, "⚡"),
                "time":        f"{ev.time.day} {ev.time.strftime('%b %Y — %H:%M')}",  # %-d is glibc-only
                "victim_avatar": ev.victim_avatar or "",
                "victim_org":    ev.victim_org or "",
                "anonymous":     ev.anonymous,
            })

        # ── Top killer ────────────────────────────────────────────────────────
        top_row = (
            qs.values("killer_name").annotate(total=Count("id")).order_by("-total").first()
        )
        top_killer = None
        if top_row:
            tk = top_row["killer_name"]
            breakdown = [
                {
                    "key": k, "label": lbl,
                    "color": CRIME_COLORS.get(k, "#8a96ab"),
                    "count": qs.filter(killer_name=tk, crime_type=k).count(),
                }
                for k, lbl in CRIME_TYPES
            ]
            top_killer = {"name": tk, "total": top_row["total"], "breakdown": breakdown}

        # ── Most hunted (victim orgs, top 5) ─────────────────────────────────
        most_hunted = list(
            qs.exclude(victim_org="")
              .values("victim_org")
              .annotate(count=Count("id"))
              .order_by("-count")[:5]
        )

        # ── Chief victim ──────────────────────────────────────────────────────
        cv_row = qs.values("victim").annotate(count=Count("id")).order_by("-count").first()
        chief_victim = None
        if cv_row:
            cv_killers = list(
                qs.filter(victim=cv_row["victim"])
                  .values("killer_name")
                  .annotate(count=Count("id"))
                  .order_by("-count")[:5]
            )
            chief_victim = {
                "name": cv_row["victim"],
                "count": cv_row["count"],
                "top_killers": cv_killers,
            }

        # ── Kill locations (top 10) ───────────────────────────────────────────
        loc_rows = (
            qs.exclude(zone="").exclude(zone="FPS")
              .values("zone")
              .annotate(count=Count("id"))
              .order_by("-count")[:10]
        )
        kill_locations = [
            {
                "name":  r["zone"],
                "count": r["count"],
                "pct":   round(r["count"] / total_kills * 100) if total_kills else 0,
            }
            for r in loc_rows
        ]

        return JsonResponse({
            "window":         window,
            "crime_rankings": crime_rankings,
            "crime_points":   crime_points_qs,
            "global_feed":    global_feed,
            "top_killer":     top_killer,
            "most_hunted":    most_hunted,
            "chief_victim":   chief_victim,
            "kill_locations": kill_locations,
            "totals": {
                "total_kills":    total_kills,
                "total_incaps":   incap_qs.count(),
                "unique_killers": qs.values("killer_name").distinct().count(),
                "unique_victims": qs.values("victim").distinct().count(),
            },
        })


# ── Desktop device-login flow (OAuth2 Device Authorization Grant, RFC 8628) ──────────────
DEVICE_CODE_TTL = 600       # seconds the user has to approve in the browser
DEVICE_POLL_INTERVAL = 5    # seconds the client should wait between polls


def _new_user_code() -> str:
    """Short, human-visible code shown in the browser (uppercased hex). Uniqueness checked by caller."""
    return secrets.token_hex(4).upper()


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(ratelimit(key="ip", rate="20/m", method="POST", block=True), name="post")  # SEC-04
class DeviceStartView(View):
    """POST /killtracker/device/start — desktop app begins login; returns codes + verify URL."""

    def post(self, request):
        denied = _require_killtracker_client(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        device_name = (body.get("device_name") or "").strip()[:128]
        device_code = _gen_key()  # 256-bit secret the client keeps; only its hash is stored
        user_code = _new_user_code()
        for _ in range(5):        # avoid the (tiny) chance of a user_code collision
            if not DeviceAuthRequest.objects.filter(user_code=user_code).exists():
                break
            user_code = _new_user_code()
        DeviceAuthRequest.objects.create(
            device_code_hash=hash_key(device_code),
            user_code=user_code,
            device_name=device_name,
            expires_at=timezone.now() + timedelta(seconds=DEVICE_CODE_TTL),
        )
        verify_url = request.build_absolute_uri(
            reverse("killtracker:device_authorize") + f"?code={user_code}"
        )
        return JsonResponse({
            "device_code": device_code,
            "user_code": user_code,
            "verification_url": verify_url,
            "interval": DEVICE_POLL_INTERVAL,
            "expires_in": DEVICE_CODE_TTL,
        })


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(ratelimit(key="ip", rate="120/m", method="POST", block=True), name="post")  # SEC-04
class DevicePollView(View):
    """POST /killtracker/device/poll — app polls until approved, then gets the one-time token."""

    def post(self, request):
        denied = _require_killtracker_client(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        device_code = (body.get("device_code") or request.headers.get("Authorization", "")).strip()
        if not device_code:
            return JsonResponse({"status": "error", "reason": "missing device_code"}, status=400)
        try:
            dar = DeviceAuthRequest.objects.select_related("user").get(device_code_hash=hash_key(device_code))
        except DeviceAuthRequest.DoesNotExist:
            return JsonResponse({"status": "denied"}, status=404)

        if dar.status == DeviceAuthRequest.DELIVERED:
            return JsonResponse({"status": "expired"}, status=400)  # token already handed out once
        if dar.is_expired:
            return JsonResponse({"status": "expired"}, status=400)
        if dar.status == DeviceAuthRequest.PENDING:
            return JsonResponse({"status": "pending"}, status=200)
        if dar.status == DeviceAuthRequest.DENIED:
            return JsonResponse({"status": "denied"}, status=200)

        # status == APPROVED -> re-check the gate, mint the token ONCE, mark delivered.
        if not dar.user or not dar.user.has_perm("killtracker.use_tracker"):
            dar.status = DeviceAuthRequest.DENIED
            dar.save(update_fields=["status"])
            return JsonResponse({"status": "denied", "reason": "no tracker access"}, status=200)

        raw = _gen_key()
        label = (dar.device_name or "KillTracker desktop")[:64]
        key = ApiKey.objects.create(
            user=dar.user, label=label,
            auto_renew=True, key_hash=hash_key(raw), key_prefix=raw[:8],
        )
        dar.issued_key = key
        dar.status = DeviceAuthRequest.DELIVERED
        dar.save(update_fields=["issued_key", "status"])
        return JsonResponse({"status": "approved", "token": raw})


@method_decorator(login_required, name="dispatch")
class DeviceAuthorizeView(View):
    """GET/POST /killtracker/device — browser approval page (user is Discord-authed here)."""
    template_name = "killtracker/device_authorize.html"

    def _lookup(self, user_code):
        return DeviceAuthRequest.objects.filter(user_code=(user_code or "").strip().upper()).first()

    def _state(self, dar, has_access):
        if not dar or dar.status == DeviceAuthRequest.DENIED:
            return "invalid"
        if dar.status in (DeviceAuthRequest.APPROVED, DeviceAuthRequest.DELIVERED):
            return "done"
        if dar.is_expired:
            return "expired"
        if not has_access:
            return "no_access"
        return "ready"

    def get(self, request):
        code = request.GET.get("code", "")
        dar = self._lookup(code)
        has_access = request.user.has_perm("killtracker.use_tracker")
        return render(request, self.template_name, {
            "code": code, "state": self._state(dar, has_access), "has_access": has_access,
        })

    def post(self, request):
        code = request.POST.get("code", "")
        dar = self._lookup(code)
        has_access = request.user.has_perm("killtracker.use_tracker")
        if dar and not dar.is_expired and dar.status == DeviceAuthRequest.PENDING and has_access:
            dar.user = request.user
            dar.status = DeviceAuthRequest.APPROVED
            dar.save(update_fields=["user", "status"])
            return render(request, self.template_name, {"code": code, "state": "done", "has_access": True})
        return render(request, self.template_name, {
            "code": code, "state": self._state(dar, has_access), "has_access": has_access,
        })
