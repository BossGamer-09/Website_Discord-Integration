from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    IntPreferenceDefinition,
    BoolChoicePreferenceDefinition,
    CharPreferenceDefinition,
)


@global_preference
class MeritStaffChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MeritStaffChannelID"
    initial_description = "Channel where new merit/money requests are posted for staff review."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class MeritReminderDays(IntPreferenceDefinition):
    initial_name = "MeritReminderDays"
    initial_description = (
        "Days after approval before the bot sends a reminder to staff to fulfill the request. "
        "Set to 0 to disable reminders."
    )
    default_value = 2


@global_preference
class MeritNotificationsEnabled(BoolChoicePreferenceDefinition):
    initial_name = "MeritNotificationsEnabled"
    initial_description = "When ON, the bot DMs requesters when their request is approved, fulfilled, or denied."
    default_value = True


@global_preference
class MeritFriendCheckUsername(CharPreferenceDefinition):
    initial_name = "MeritFriendCheckUsername"
    initial_description = (
        "RSI username members must have on their friends list before submitting a merit request. "
        "Leave blank to disable the gate."
    )
    default_value = "discoDingo"


@global_preference
class MeritFriendCheckRSIUrl(CharPreferenceDefinition):
    initial_name = "MeritFriendCheckRSIUrl"
    initial_description = "Full RSI profile URL shown on the friends-list gate page."
    default_value = "https://robertsspaceindustries.com/en/citizens/discoDingo"
