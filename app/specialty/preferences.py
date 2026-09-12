from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    BoolChoicePreferenceDefinition,
)


@global_preference
class SpecialtyApprovalChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "SpecialtyApprovalChannelID"
    initial_description = "Channel where specialty grant requests are posted for staff approval."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class SpecialtyAchievementsChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "SpecialtyAchievementsChannelID"
    initial_description = "Channel where approved specialty announcements are posted."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class SpecialtyGrantingEnabled(BoolChoicePreferenceDefinition):
    initial_name = "SpecialtyGrantingEnabled"
    initial_description = "When OFF, the specialty granting panel is disabled."
    default_value = True
