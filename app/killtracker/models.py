import hashlib
import secrets
from django.conf import settings
from django.core.cache import caches
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

GAME_DATA_CACHE_KEY = "killtracker.game_data.v1"  # bumped by any GameDataMapping/GameDataList/ExclusionRule write


def invalidate_game_data_cache():
    """Called on any write to the game-data tables so /api/server/data/all reflects it
    immediately — no cache TTL to wait out, no server restart."""
    caches["default"].delete(GAME_DATA_CACHE_KEY)


def _gen_key() -> str:
    return secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    """SHA-256 of a raw API token. The token is 256-bit, so a fast hash is sufficient."""
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKey(models.Model):
    """KillTracker client API key. FK → OrgPlayer (settings.AUTH_USER_MODEL)."""
    user         = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="killtracker_keys", on_delete=models.CASCADE)
    key_hash     = models.CharField(max_length=64, unique=True, db_index=True)  # SEC-03: sha256 of the raw key
    key_prefix   = models.CharField(max_length=12, blank=True, default="", help_text="First chars of the raw key, for identification only.")
    label        = models.CharField(max_length=64, blank=True, default="")
    is_permanent = models.BooleanField(default=False)
    auto_renew   = models.BooleanField(default=False, help_text="If true, expiry is extended each time the key is used.")
    revoked      = models.BooleanField(default=False, db_index=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    history      = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_apikey"
        ordering  = ["-created_at"]
        permissions = [
            ("can_generate_key",  "Can generate a KillTracker API key"),
            ("can_view_leaderboard", "Can view the KillTracker leaderboard"),
            ("can_manage_aliases", "Can manage KillTracker player aliases"),
        ]

    def __str__(self):
        return f"{self.user} — {self.label or self.key_prefix}"


class DeviceAuthRequest(models.Model):
    """OAuth2 device-flow (RFC 8628) record for the KillTracker desktop login.

    Desktop app starts a flow -> user approves in the browser (Discord-authed there, checked for
    killtracker.use_tracker) -> app polls and receives a one-time token (a minted ApiKey).
    Only the SHA-256 of the device_code is stored; the raw token is returned exactly once.
    """
    PENDING   = "pending"
    APPROVED  = "approved"
    DELIVERED = "delivered"
    DENIED    = "denied"

    device_code_hash = models.CharField(max_length=64, unique=True, db_index=True)  # sha256(device_code)
    user_code        = models.CharField(max_length=12, unique=True, db_index=True)  # short code shown in browser
    device_name      = models.CharField(max_length=128, blank=True, default="")     # client hostname -> key label
    status           = models.CharField(max_length=12, default=PENDING)
    user             = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="killtracker_device_auths")
    issued_key       = models.ForeignKey("killtracker.ApiKey", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at       = models.DateTimeField(auto_now_add=True)
    expires_at       = models.DateTimeField(db_index=True)

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_device_auth"
        ordering  = ["-created_at"]

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"DeviceAuth {self.user_code} ({self.status})"


class PlayerAlias(models.Model):
    """Whitelist: maps an SC in-game handle to an OrgPlayer (replaces JS WHITELISTED_PLAYERS)."""
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="killtracker_aliases", on_delete=models.CASCADE)
    game_name  = models.CharField(max_length=64, unique=True, db_index=True)
    note       = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    history    = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_player_alias"
        verbose_name_plural = "Player aliases"

    def __str__(self):
        return f"{self.game_name} → {self.user}"


class BlacklistEntry(models.Model):
    """Blocked SC handle — kills from/for this handle are dropped."""
    game_name  = models.CharField(max_length=64, unique=True, db_index=True)
    reason     = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_blacklist"
        verbose_name_plural = "Blacklist entries"

    def __str__(self):
        return self.game_name


class GameModeChannel(models.Model):
    """Maps a KillTracker game_mode key → Discord channel ID + display label."""
    game_mode_key = models.CharField(max_length=64, unique=True)
    display_name  = models.CharField(max_length=64)
    channel_id    = models.BigIntegerField()
    is_default    = models.BooleanField(default=False, help_text="Fallback if no key matches")

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_gamemode_channel"

    def __str__(self):
        return f"{self.game_mode_key} → {self.display_name}"


class KillEvent(models.Model):
    """Recorded kill event — triggers signal → Redis → cog embed."""
    killer        = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="killtracker_kills", on_delete=models.SET_NULL, null=True, blank=True)
    killer_name   = models.CharField(max_length=64, db_index=True)
    victim        = models.CharField(max_length=64, db_index=True)
    game_mode     = models.CharField(max_length=64, default="SC_Default")
    weapon        = models.CharField(max_length=128, blank=True, default="")
    zone          = models.CharField(max_length=128, blank=True, default="")
    killers_ship  = models.CharField(max_length=128, blank=True, default="")
    client_ver    = models.CharField(max_length=16, blank=True, default="")
    time          = models.DateTimeField(default=timezone.now, db_index=True)

    victim_org        = models.CharField(max_length=128, blank=True, default="")
    victim_org_sid    = models.CharField(max_length=32, blank=True, default="")
    victim_avatar     = models.URLField(blank=True, default="")
    victim_enlisted   = models.CharField(max_length=32, blank=True, default="")
    victim_location   = models.CharField(max_length=128, blank=True, default="")

    crime_type    = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # Downed-but-not-confirmed-dead (SC medical system). Announced as an incap, excluded from
    # kill leaderboards. Only 0-HP edges the client's parser confirmed via a Dead marker
    # arrive with incap=False.
    incap         = models.BooleanField(default=False, db_index=True)
    anonymous     = models.BooleanField(default=False)
    api_key       = models.ForeignKey(ApiKey, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    created_at    = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_kill_event"
        ordering  = ["-time"]
        permissions = [
            # Gate for the desktop client. Grant this to the Django group synced from the
            # BlightVeil "tracker" Discord role; losing the role -> dropped from the group ->
            # loses this perm -> the next ingest call 403s and the client goes dark.
            ("use_tracker", "Can use the KillTracker desktop client"),
        ]
        indexes = [
            models.Index(fields=["killer", "-time"]),
            models.Index(fields=["game_mode", "-time"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["killer_name", "victim", "time", "game_mode"],
                name="killtracker_dedupe",
            ),
        ]

    def __str__(self):
        return f"{self.killer_name} → {self.victim} @ {self.time:%Y-%m-%d %H:%M}"


class ParserRelease(models.Model):
    """Server-pushed override for the desktop client's Game.log combat parser module.

    The client validates `source` (must parse as Python and define GameLogParser + Event)
    before exec'ing it, and keeps its last-good parser on any failure — so publishing a bad
    release here can't crash clients already running, it just gets ignored until fixed. Only
    one row should be active at a time; save() enforces that automatically.
    """
    version    = models.CharField(max_length=64, unique=True, help_text="Shown in client logs; bump on every change so clients pick it up.")
    source     = models.TextField(help_text="Full Python source for modules/sc_parser.py — must define GameLogParser and Event.")
    signature  = models.CharField(
        max_length=128, blank=True, default="",
        help_text=(
            "Hex Ed25519 signature of the source, made with the OFFLINE parser-signing key "
            "(tools/sign_parser_release.py in the Killtracker repo). Clients that ship a "
            "public key refuse any release whose signature doesn't verify — so a compromised "
            "server/admin account can't push code to users' machines."
        ),
    )
    is_active  = models.BooleanField(default=False, db_index=True)
    notes      = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    history    = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_parser_release"
        ordering  = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.is_active:
            ParserRelease.objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.version} ({'active' if self.is_active else 'inactive'})"


class ClientRelease(models.Model):
    """A published desktop-client build (the .exe), hosted by Servitor.

    The active release drives the in-app auto-updater: /data/all advertises its version and
    the gated download endpoint; running clients download it in the background and offer a
    one-click "restart to update". Logged-in members with tracker access can also grab it
    from their KillTracker dashboard page. Only one release should be active; save() enforces it.
    """
    version    = models.CharField(max_length=64, unique=True, help_text="Must match the exe's local_version (e.g. '1.7'). Clients update when this is greater than theirs.")
    file       = models.FileField(upload_to="killtracker/releases/", help_text="The 'BlightVeil KillTracker.exe' (WITH sounds). Clients running the with-sounds build get this.")
    file_nosound = models.FileField(upload_to="killtracker/releases/", blank=True, help_text="The 'BlightVeil KillTracker (no sounds).exe'. Clients running the no-sounds build get this; falls back to the with-sounds file if left empty.")
    sha256         = models.CharField(max_length=64, blank=True, default="", editable=False, help_text="Auto-computed SHA-256 of the with-sounds exe; the client verifies its download against this.")
    sha256_nosound = models.CharField(max_length=64, blank=True, default="", editable=False, help_text="Auto-computed SHA-256 of the no-sounds exe.")
    is_active  = models.BooleanField(default=False, db_index=True)
    notes      = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    history    = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_client_release"
        ordering  = ["-created_at"]

    @staticmethod
    def _digest(fieldfile) -> str:
        if not fieldfile:
            return ""
        h = hashlib.sha256()
        # Read from the START without closing the underlying file — closing a still-in-flight
        # TemporaryUploadedFile deletes its /tmp file out from under Django's save().
        fieldfile.open("rb")
        try:
            fieldfile.seek(0)
            for chunk in iter(lambda: fieldfile.read(1024 * 1024), b""):
                h.update(chunk)
            fieldfile.seek(0)
        finally:
            pass
        return h.hexdigest()

    def save(self, *args, **kwargs):
        if self.is_active:
            ClientRelease.objects.exclude(pk=self.pk).update(is_active=False)
        # Persist the row + move uploads into storage FIRST, then hash the stored files and
        # write the digests back with a queryset update (no second full save / history row,
        # and nothing touches the temporary upload file).
        super().save(*args, **kwargs)
        new_sha = self._digest(self.file)
        new_sha_ns = self._digest(self.file_nosound)
        if new_sha != self.sha256 or new_sha_ns != self.sha256_nosound:
            self.sha256, self.sha256_nosound = new_sha, new_sha_ns
            type(self).objects.filter(pk=self.pk).update(
                sha256=new_sha, sha256_nosound=new_sha_ns,
            )

    def __str__(self):
        return f"{self.version} ({'active' if self.is_active else 'inactive'})"


class GameDataMapping(models.Model):
    """Generic key -> value lookup table for desktop-client reference data that used to be
    hardcoded Python constants in game_data.py (WEAPON_MAPPING, SHIP_MAPPING, the body-part-ID
    zone map, OS process names the client looks for, contract-log regexes, RSI-profile scraper
    CSS selectors). Editable here so a game patch or CIG log-format change is an admin edit,
    not a code deploy + server restart."""
    CATEGORY_WEAPON = "weapon"
    CATEGORY_SHIP = "ship"
    CATEGORY_PART_MAP = "part_map"
    CATEGORY_PROCESS_NAME = "process_name"
    CATEGORY_CONTRACT_PATTERN = "contract_pattern"
    CATEGORY_RSI_SELECTOR = "rsi_selector"
    CATEGORY_CHOICES = [
        (CATEGORY_WEAPON, "Weapon name"),
        (CATEGORY_SHIP, "Ship / zone name"),
        (CATEGORY_PART_MAP, "Body part ID -> zone"),
        (CATEGORY_PROCESS_NAME, "OS process name"),
        (CATEGORY_CONTRACT_PATTERN, "Contract log regex"),
        (CATEGORY_RSI_SELECTOR, "RSI profile CSS selector"),
    ]
    category   = models.CharField(max_length=32, choices=CATEGORY_CHOICES, db_index=True)
    key        = models.CharField(max_length=256)
    value      = models.TextField()
    history    = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_game_data_mapping"
        unique_together = ("category", "key")
        ordering  = ["category", "key"]

    def __str__(self):
        return f"[{self.category}] {self.key} = {self.value}"


class GameDataList(models.Model):
    """Generic category -> list-of-values table for desktop-client reference data that's just a
    flat list (ignored-victim substrings/prefixes, ship manufacturer prefixes, required
    user.cfg lines, Free Flight game-mode keys). Same rationale as GameDataMapping."""
    CATEGORY_IGNORED_VICTIM_SUBSTRING = "ignored_victim_substring"
    CATEGORY_IGNORED_VICTIM_PREFIX = "ignored_victim_prefix"
    CATEGORY_SHIP_MANUFACTURER_PREFIX = "ship_manufacturer_prefix"
    CATEGORY_USER_CFG_LINE = "user_cfg_line"
    CATEGORY_FREE_FLIGHT_GAME_MODE = "free_flight_game_mode"
    CATEGORY_CHOICES = [
        (CATEGORY_IGNORED_VICTIM_SUBSTRING, "Ignored victim substring"),
        (CATEGORY_IGNORED_VICTIM_PREFIX, "Ignored victim prefix"),
        (CATEGORY_SHIP_MANUFACTURER_PREFIX, "Ship manufacturer prefix"),
        (CATEGORY_USER_CFG_LINE, "user.cfg required line"),
        (CATEGORY_FREE_FLIGHT_GAME_MODE, "Free Flight game mode key"),
    ]
    category   = models.CharField(max_length=32, choices=CATEGORY_CHOICES, db_index=True)
    value      = models.CharField(max_length=256)
    history    = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_game_data_list"
        unique_together = ("category", "value")
        ordering  = ["category", "value"]

    def __str__(self):
        return f"[{self.category}] {self.value}"


class ExclusionRule(models.Model):
    """A rule the desktop client uses to drop a parsed kill that isn't real PvP (a ship reset,
    self-destruct, etc.): drop the kill if `substring` appears in the raw log line while the
    player is in `game_mode`."""
    game_mode  = models.CharField(max_length=64)
    substring  = models.CharField(max_length=128)
    message    = models.CharField(max_length=256, help_text="Logged client-side when this rule drops a kill.")
    history    = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_exclusion_rule"
        ordering  = ["game_mode", "substring"]

    def __str__(self):
        return f"{self.game_mode}: {self.substring}"


# Signals (not per-model save()/delete() overrides) so bulk operations — like the admin's
# "Delete selected" action, which uses QuerySet.delete() and skips instance methods — still
# invalidate the cache. post_save/post_delete fire per-object even for a queryset delete.
def _invalidate_game_data_cache(sender, **kwargs):
    invalidate_game_data_cache()


for _model in (GameDataMapping, GameDataList, ExclusionRule):
    models.signals.post_save.connect(_invalidate_game_data_cache, sender=_model, weak=False)
    models.signals.post_delete.connect(_invalidate_game_data_cache, sender=_model, weak=False)


class RSIProfileSnapshot(models.Model):
    """Tracks last-seen RSI profile values per handle for name-change detection."""
    handle              = models.CharField(max_length=64, unique=True, db_index=True)
    display_name        = models.CharField(max_length=128, blank=True, default="")
    org_name            = models.CharField(max_length=128, blank=True, default="")
    org_short           = models.CharField(max_length=32, blank=True, default="")
    avatar_url          = models.URLField(blank=True, default="")
    enlisted_date       = models.CharField(max_length=32, blank=True, default="")
    first_seen_at       = models.DateTimeField(auto_now_add=True)
    last_checked_at     = models.DateTimeField(auto_now=True)
    history             = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "killtracker"
        db_table  = "killtracker_rsi_profile_snapshot"

    def __str__(self):
        return f"{self.handle} ({self.display_name})"
