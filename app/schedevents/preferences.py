"""
app/schedevents/preferences.py

Runtime configuration for the scheduled events system.
No hardcoded channel/role/guild IDs in cog logic — all live here.
"""
from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    IntPreferenceDefinition,
    CharPreferenceDefinition,
    BoolChoicePreferenceDefinition,
)


# ---------------------------------------------------------------------------
# Display / listing
# ---------------------------------------------------------------------------

@global_preference
class EventListingTimezone(CharPreferenceDefinition):
    initial_name = "Event Listing Timezone"
    initial_description = (
        "IANA timezone used when displaying times in the /events listing command. "
        "E.g. 'America/New_York', 'Europe/London'. Defaults to UTC."
    )
    default_value = "UTC"


# ---------------------------------------------------------------------------
# Channel IDs
# ---------------------------------------------------------------------------

@global_preference
class EventAnnouncementChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Event Announcement Channel ID"
    initial_description = (
        "Text channel where new published events are announced with the RSVP embed. "
        "This is the primary public-facing event channel."
    )
    channel_types_filter = ["text"]
    default_value = None


@global_preference
class EventStaffLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Event Staff Log Channel ID"
    initial_description = (
        "Staff-only channel where event lifecycle events are logged: "
        "creation, status changes, RSVP counts, and post-event reports."
    )
    channel_types_filter = ["text"]
    default_value = None


@global_preference
class EventReminderFallbackChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Event Reminder Fallback Channel ID"
    initial_description = (
        "Channel used for CHANNEL-type reminders that do not specify their own channel. "
        "Also used as fallback when a user has DMs disabled."
    )
    channel_types_filter = ["text"]
    default_value = None


# ---------------------------------------------------------------------------
# VC integration
# ---------------------------------------------------------------------------

@global_preference
class EventVCParentCategoryID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Event VC Parent Category ID"
    initial_description = (
        "Discord category where event voice channels are created. "
        "Leave blank to inherit the category from the VC profile's source channel."
    )
    channel_types_filter = ["category"]
    default_value = None


@global_preference
class EventDefaultVCProfileSlug(CharPreferenceDefinition):
    initial_name = "Event Default VC Profile Slug"
    initial_description = (
        "Slug of the VoiceChannelProfile used when spawning an event VC and the "
        "EventPlan does not specify one. Leave blank to use the lowest-priority active profile."
    )
    default_value = ""


# ---------------------------------------------------------------------------
# Attendance / check-in behaviour
# ---------------------------------------------------------------------------

@global_preference
class EventLateThresholdMinutes(IntPreferenceDefinition):
    initial_name = "Event Late Threshold (minutes)"
    initial_description = (
        "How many minutes after event start a VC join is still counted as ON TIME. "
        "Joins after this threshold are marked LATE. Default: 10."
    )
    default_value = 10


@global_preference
class EventAutoCheckinEnabled(BoolChoicePreferenceDefinition):
    initial_name = "Event Auto Check-in Enabled"
    initial_description = (
        "When enabled, a member joining the event's voice channel is automatically "
        "checked in on their RSVP (if GOING or WAITLISTED)."
    )
    default_value = True


# ---------------------------------------------------------------------------
# Reminder defaults
# ---------------------------------------------------------------------------

@global_preference
class EventDefaultReminderMinutes(IntPreferenceDefinition):
    initial_name = "Event Default Reminder (minutes before)"
    initial_description = (
        "Default DM reminder sent to all GOING/MAYBE RSVPs N minutes before event start. "
        "Set to 0 to disable. Applied automatically when an event is published."
    )
    default_value = 30


@global_preference
class EventPostSummaryToAnnouncementChannel(BoolChoicePreferenceDefinition):
    initial_name = "Post Event Summary to Announcement Channel"
    initial_description = (
        "When ON, a brief public post-event summary embed is also posted to the event's "
        "announcement channel when the event completes (in addition to the staff log report)."
    )
    default_value = True


# ---------------------------------------------------------------------------
# Announcement / mention defaults
# ---------------------------------------------------------------------------

@global_preference
class EventHideAttendeeNamesGlobal(BoolChoicePreferenceDefinition):
    initial_name = "Hide Attendee Names Globally"
    initial_description = (
        "When enabled, attendee names are hidden on all event embeds by default. "
        "Individual events can override this."
    )
    default_value = False


@global_preference
class EventCleanMessages(BoolChoicePreferenceDefinition):
    initial_name = "Clean Event Command Messages"
    initial_description = (
        "Automatically delete command invocation messages after event commands run."
    )
    default_value = False


@global_preference
class EventDefaultMentionsOnCreate(CharPreferenceDefinition):
    initial_name = "Default Mentions on Event Create"
    initial_description = (
        "Comma-separated Discord role IDs to mention when any event is published. "
        "Leave blank to disable. Individual events can override."
    )
    default_value = ""


@global_preference
class EventDefaultMentionsOnStart(CharPreferenceDefinition):
    initial_name = "Default Mentions on Event Start"
    initial_description = (
        "Comma-separated Discord role IDs to mention when any event goes ACTIVE. "
        "Leave blank to disable. Individual events can override."
    )
    default_value = ""


# ---------------------------------------------------------------------------
# Weekly event digest thread
# ---------------------------------------------------------------------------

@global_preference
class WeeklyDigestChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Weekly Event Digest Channel ID"
    initial_description = (
        "Text channel where the weekly event digest thread/message is posted or updated. "
        "Leave blank to disable the weekly digest."
    )
    channel_types_filter = ["text"]
    default_value = None


@global_preference
class WeeklyDigestMessageID(IntPreferenceDefinition):
    initial_name = "Weekly Event Digest Message ID"
    initial_description = (
        "ID of the pinned digest message in WeeklyDigestChannelID. "
        "Set automatically by the bot — do not edit manually."
    )
    default_value = 0


@global_preference
class WeeklyDigestLookaheadDays(IntPreferenceDefinition):
    initial_name = "Weekly Digest Lookahead (days)"
    initial_description = (
        "How many days ahead to include events in the weekly digest. Default: 7."
    )
    default_value = 7


# ---------------------------------------------------------------------------
# ICS / Calendar sync
# ---------------------------------------------------------------------------

@global_preference
class EventICSEnabled(BoolChoicePreferenceDefinition):
    initial_name = "ICS Calendar Export Enabled"
    initial_description = (
        "Expose an ICS feed URL that members can subscribe to in Google Calendar, "
        "Apple Calendar, etc. Shows all published/active events."
    )
    default_value = True
