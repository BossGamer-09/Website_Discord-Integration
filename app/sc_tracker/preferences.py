import os

from app.preferences.utils import global_preference
from app.preferences.types import BoolChoicePreferenceDefinition, IntChoicePreferenceDefinition, CharPreferenceDefinition, IntPreferenceDefinition, StrChoicePreferenceDefinition


# ── Hangar @global_preference classes ────────────────────────────────────────

@global_preference
class HangarEnabled(BoolChoicePreferenceDefinition):
    initial_name        = "Hangar Enabled"
    initial_description = "Enable/disable the Executive Hangar timer embed and Celery tasks."
    default_value             = True


@global_preference
class HangarT0Ms(IntPreferenceDefinition):
    initial_name        = "Hangar T0 (ms)"
    initial_description = (
        "INITIAL_OPEN_TIME in Unix milliseconds from exec.xyxyll.com/app.js. "
        "Update after every Star Citizen patch."
    )
    default_value             = 1_774_501_916_500


@global_preference
class HangarOpenMs(IntPreferenceDefinition):
    initial_name        = "Hangar Open Duration (ms)"
    initial_description = "OPEN_DURATION in milliseconds from exec.xyxyll.com/app.js."
    default_value             = 3_900_338


@global_preference
class HangarCloseMs(IntPreferenceDefinition):
    initial_name        = "Hangar Close Duration (ms)"
    initial_description = "CLOSE_DURATION in milliseconds from exec.xyxyll.com/app.js."
    default_value             = 7_200_623


@global_preference
class HangarPatchInfo(CharPreferenceDefinition):
    initial_name        = "Hangar Patch Info"
    initial_description = "Human-readable patch label shown in hangar embeds."
    default_value             = "Updated Mar 27, 2026 for Star Citizen Patch 4.7.0-LIVE (Server Version 11518367)"


# ── RSI Status @global_preference classes ────────────────────────────────────

@global_preference
class StatusEnabled(BoolChoicePreferenceDefinition):
    initial_name        = "RSI Status Enabled"
    initial_description = "Enable/disable RSI status polling, embeds, and DM dispatching."
    default_value             = True


@global_preference
class StatusNewDedupeHours(IntPreferenceDefinition):
    initial_name        = "Status New-Issue Dedupe Window (hours)"
    initial_description = (
        "How many hours must pass before re-notifying subscribers about the same "
        "new incident. Prevents duplicate DMs on rapid polling."
    )
    default_value             = 12


@global_preference
class StatusUpdateDedupeHours(IntPreferenceDefinition):
    initial_name        = "Status Update Dedupe Window (hours)"
    initial_description = (
        "How many hours must pass before re-notifying subscribers about an update "
        "to the same incident."
    )
    default_value             = 6


@global_preference
class StatusDmCleanupHours(IntPreferenceDefinition):
    initial_name        = "Status DM Cleanup Age (hours)"
    initial_description = (
        "DM records older than this many hours are deleted by the hourly cleanup task "
        "and their Discord messages are removed."
    )
    default_value             = 24


# ── SCTrackerPreferences (cogs / views) ───────────────────────────────────────

def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val).lower() in ("true", "1", "yes")


def _get_config():
    try:
        from app.sc_tracker.models import SCTrackerConfig
        return SCTrackerConfig.get()
    except Exception:
        return None


class SCTrackerPreferences:
    @property
    def hangar_channel_id(self) -> int:
        cfg = _get_config()
        if cfg and cfg.hangar_channel_id:
            return cfg.hangar_channel_id
        return _env_int("SC_HANGAR_CHANNEL_ID", 0)

    @property
    def status_channel_id(self) -> int:
        cfg = _get_config()
        if cfg and cfg.status_channel_id:
            return cfg.status_channel_id
        return _env_int("SC_STATUS_CHANNEL_ID", 0)

    @property
    def updates_channel_id(self) -> int:
        cfg = _get_config()
        if cfg and cfg.updates_channel_id:
            return cfg.updates_channel_id
        return _env_int("SC_UPDATES_CHANNEL_ID", 0)

    @property
    def hangar_phase_role_id(self) -> int:
        cfg = _get_config()
        if cfg and cfg.hangar_phase_role_id:
            return cfg.hangar_phase_role_id
        return _env_int("SC_HANGAR_PHASE_ROLE_ID", 0)

    @property
    def hangar_light_role_id(self) -> int:
        cfg = _get_config()
        if cfg and cfg.hangar_light_role_id:
            return cfg.hangar_light_role_id
        return _env_int("SC_HANGAR_LIGHT_ROLE_ID", 0)

    @property
    def hangar_open_ms(self) -> int:
        cfg = _get_config()
        if cfg and cfg.hangar_open_ms:
            return cfg.hangar_open_ms
        return _env_int("SC_HANGAR_OPEN_MS", 3_900_338)

    @property
    def hangar_close_ms(self) -> int:
        cfg = _get_config()
        if cfg and cfg.hangar_close_ms:
            return cfg.hangar_close_ms
        return _env_int("SC_HANGAR_CLOSE_MS", 7_200_623)

    @property
    def hangar_t0_ms(self) -> int:
        cfg = _get_config()
        if cfg and cfg.hangar_t0_ms:
            return cfg.hangar_t0_ms
        return _env_int("SC_HANGAR_T0_MS", 1_774_501_916_500)

    @property
    def hangar_patch_info(self) -> str:
        cfg = _get_config()
        if cfg and cfg.hangar_patch_info:
            return cfg.hangar_patch_info
        return _env_str(
            "SC_HANGAR_PATCH_INFO",
            "Updated Mar 27, 2026 for Star Citizen Patch 4.7.0-LIVE (Server Version 11518367)",
        )

    @property
    def hangar_enabled(self) -> bool:
        cfg = _get_config()
        if cfg is not None:
            return cfg.hangar_enabled
        return _env_bool("SC_HANGAR_ENABLED", True)

    @property
    def status_enabled(self) -> bool:
        cfg = _get_config()
        if cfg is not None:
            return cfg.status_enabled
        return _env_bool("SC_STATUS_ENABLED", True)

    @property
    def hangar_embed_interval_secs(self) -> int:
        return _env_int("SC_HANGAR_EMBED_INTERVAL_SECS", 30)

    @property
    def status_poll_interval_secs(self) -> int:
        return _env_int("SC_STATUS_POLL_INTERVAL_SECS", 60)

    @property
    def hangar_sync_interval_mins(self) -> int:
        return _env_int("SC_HANGAR_SYNC_INTERVAL_MINS", 15)

    @property
    def status_dm_cleanup_hours(self) -> int:
        return _env_int("SC_STATUS_DM_CLEANUP_HOURS", 24)

    @property
    def status_new_dedupe_hours(self) -> int:
        return _env_int("SC_STATUS_NEW_DEDUPE_HOURS", 12)

    @property
    def status_update_dedupe_hours(self) -> int:
        return _env_int("SC_STATUS_UPDATE_DEDUPE_HOURS", 6)


sc_prefs = SCTrackerPreferences()