from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    BoolChoicePreferenceDefinition,
)


@global_preference
class OrgGoalsChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Org Goals Channel ID"
    initial_description = (
        "Channel where org-wide goal threads are created and status messages posted."
    )
    channel_types_filter = ["text"]
    default_value = None


@global_preference
class LeaderGoalsChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Leader Goals Channel ID"
    initial_description = (
        "Channel where per-leader goal threads are created."
    )
    channel_types_filter = ["text"]
    default_value = None


@global_preference
class GoalReminderDefaultChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Goal Reminder Default Channel ID"
    initial_description = (
        "Fallback channel for goal reminders when no override is set on the reminder."
    )
    channel_types_filter = ["text"]
    default_value = None


@global_preference
class GoalPostToThreadEnabled(BoolChoicePreferenceDefinition):
    initial_name = "Goal Post-to-Thread Enabled"
    initial_description = (
        "When ON, each new org or leader goal creates a Discord thread for updates."
    )
    default_value = True
