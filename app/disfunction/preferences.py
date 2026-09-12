"""
Runtime configuration for the Disfunction voice & attendance system.
All channel IDs and toggles live here — no hardcoded IDs in cog logic.
Read with: await aget_global_preference(PrefClass.import_path)
"""
from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    IntPreferenceDefinition,
    BoolChoicePreferenceDefinition,
)


# ---------------------------------------------------------------------------
# Parent trigger channels — join these to spawn a temp VC
# ---------------------------------------------------------------------------

@global_preference
class VCParentMainID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "VC Parent Channel ID"
    initial_description = (
        "Discord channel ID for the single lobby/trigger channel. "
        "Joining this creates a temp VC whose type is determined by the member's highest permission."
    )
    channel_types_filter = ["voice"]
    default_value = None


@global_preference
class VCParentStaffID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Staff VC Parent Channel ID"
    initial_description = (
        "Joining this channel creates a Staff-type temp VC. "
        "Leave empty to disable a dedicated staff lobby (staff can use the main parent instead)."
    )
    channel_types_filter = ["voice"]
    default_value = None


@global_preference
class VCParentLeaderID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Leader VC Parent Channel ID"
    initial_description = (
        "Joining this channel creates a Leader-type temp VC. "
        "Leave empty to disable a dedicated leader lobby."
    )
    channel_types_filter = ["voice"]
    default_value = None


# ---------------------------------------------------------------------------
# Log channels
# ---------------------------------------------------------------------------

@global_preference
class AttendanceLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Attendance Log Channel ID"
    initial_description = "Attendance summaries are posted to this channel when an event ends."
    default_value = None


@global_preference
class VoiceBanLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Voice Ban Log Channel ID"
    initial_description = "Voice ban / unban actions are logged to this channel."
    default_value = None


# ---------------------------------------------------------------------------
# VC activity log
# ---------------------------------------------------------------------------

@global_preference
class VCActivityLogEnabled(BoolChoicePreferenceDefinition):
    initial_name = "VC Activity Log Enabled"
    initial_description = (
        "Post join / leave / move notifications in each voice channel's own text area. "
        "Forced moves show who moved the user."
    )
    default_value = True


# ---------------------------------------------------------------------------
# Cleanup / behaviour
# ---------------------------------------------------------------------------

@global_preference
class VCCleanupDelaySecs(IntPreferenceDefinition):
    initial_name = "VC Cleanup Delay (seconds)"
    initial_description = (
        "Seconds after a temp VC becomes empty before it is deleted. "
        "Default 5. Set to 0 for immediate deletion."
    )
    default_value = 5


@global_preference
class RedChannelWarningEnabled(BoolChoicePreferenceDefinition):
    initial_name = "Red Channel Warning Enabled"
    initial_description = (
        "Whether to DM members a warning when they join a RED/Knights channel for the first time."
    )
    default_value = True


@global_preference
class VCBitrateMax(IntPreferenceDefinition):
    initial_name = "VC Max Bitrate (bps)"
    initial_description = "Maximum allowed bitrate for user-created temp VCs (in bps). Default 384000."
    default_value = 384000


@global_preference
class VCUserLimitMax(IntPreferenceDefinition):
    initial_name = "VC Max User Limit"
    initial_description = "Maximum user limit a member can set on their temp VC. Default 99."
    default_value = 99


# ---------------------------------------------------------------------------
# Nomination system
# ---------------------------------------------------------------------------

@global_preference
class NominationApprovalChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Nomination Approval Channel ID"
    initial_description = "Pending nominations are posted here for staff to approve/deny."
    default_value = None


@global_preference
class NominationAnnouncementChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Nomination Announcement Channel ID"
    initial_description = "Approved nominations and level-up announcements are posted here."
    default_value = None


@global_preference
class NominationEmbedChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Nomination Embed Channel ID"
    initial_description = "Channel where the persistent Distinction Levels & Nominations embed is posted."
    default_value = None


# ---------------------------------------------------------------------------
# Welcome system
# ---------------------------------------------------------------------------

@global_preference
class WelcomeChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Welcome Channel ID"
    initial_description = "Channel where the welcome message + banner image are posted on member join."
    default_value = None


@global_preference
class MemberJoinChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Member Join Log Channel ID"
    initial_description = "Staff channel where detailed join embeds are logged."
    default_value = None


@global_preference
class MemberLeaveChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Member Leave Log Channel ID"
    initial_description = "Staff channel where leave/kick/ban embeds are logged."
    default_value = None


@global_preference
class GatehouseRolesChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Gatehouse Roles Channel ID"
    initial_description = "Channel linked in the welcome message where new members pick roles."
    default_value = None


@global_preference
class FaqChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "FAQ Channel ID"
    initial_description = "Channel linked in the welcome message for new-member questions."
    default_value = None
