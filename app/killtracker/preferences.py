from app.preferences.utils import global_preference
from app.preferences.types import (
    BoolChoicePreferenceDefinition,
    CharPreferenceDefinition,
    IntPreferenceDefinition,
)


@global_preference
class KTEnabled(BoolChoicePreferenceDefinition):
    initial_name = "KillTracker Enabled"
    initial_description = "Master switch for accepting KillTracker reports and dispatching embeds."
    default_value = True


@global_preference
class KTRequiredClientVersion(CharPreferenceDefinition):
    initial_name = "KillTracker Required Client Version Prefix"
    initial_description = "Reject reports whose client_ver does not start with this (e.g. '1.6')."
    default_value = "1.6"


@global_preference
class KTLatestClientVersion(CharPreferenceDefinition):
    initial_name = "KillTracker Latest Client Version"
    initial_description = (
        "Newest published desktop client version (e.g. '1.6'). Served to clients in "
        "/api/server/data/all; a client with a lower version shows an update notice. "
        "The client repo is private, so the client can't ask GitHub — this is the only "
        "update channel. Leave empty to disable update notices."
    )
    default_value = ""


@global_preference
class KTClientDownloadURL(CharPreferenceDefinition):
    initial_name = "KillTracker Client Download URL"
    initial_description = "Where the update notice sends users to fetch the new exe (Discord post, website, etc.)."
    default_value = ""


@global_preference
class KTGuildID(IntPreferenceDefinition):
    initial_name = "KillTracker Guild ID"
    initial_description = "Discord guild used for display-name matching."
    default_value = 1166103102378750033


@global_preference
class KTDefaultChannelID(IntPreferenceDefinition):
    initial_name = "KillTracker Default Channel ID"
    initial_description = "Fallback channel if no GameModeChannel matches."
    default_value = 1324936929929859122


@global_preference
class KTFuzzThreshold(IntPreferenceDefinition):
    initial_name = "KillTracker Fuzz Match Threshold"
    initial_description = "Minimum fuzzball ratio to match SC handle → guild member."
    default_value = 80


@global_preference
class KTFallbackAvatar(CharPreferenceDefinition):
    initial_name = "KillTracker Fallback Avatar URL"
    initial_description = "Used when victim avatar is missing / default RSI avatar."
    default_value = "https://cdn.discordapp.com/attachments/1176596448041779270/1300872217210523648/BlightVeilArtboard_4PNG.png"


@global_preference
class KTRSISnapshotStaleDays(IntPreferenceDefinition):
    initial_name = "KillTracker RSI Snapshot Stale Days"
    initial_description = "Delete RSIProfileSnapshot rows not updated in this many days (0 = disabled)."
    default_value = 90
