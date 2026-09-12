"""
app/infantryboard/preferences.py

Runtime configuration for the infantry roster.
No hardcoded channel/guild IDs — all live here.
"""
from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    BoolChoicePreferenceDefinition,
)


@global_preference
class InfantryRosterChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Infantry Roster Channel ID"
    initial_description = (
        "Text channel where infantry roster update notifications are posted "
        "(e.g. new soldiers added, goals completed)."
    )


@global_preference
class InfantryRosterPublicUpdates(BoolChoicePreferenceDefinition):
    initial_name = "Infantry Roster Public Updates"
    initial_description = (
        "When enabled, goal completions and new-soldier additions are announced "
        "in the Infantry Roster Channel."
    )
    default_value = False


@global_preference
class InfantryLeadChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "InfantryLeadChannelID"
    initial_description = (
        "Channel where Infantry Lead is pinged when a new Soldier entry is created. "
        "Set to 0 to disable."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class InfantryAutoCreateSoldier(BoolChoicePreferenceDefinition):
    initial_name = "InfantryAutoCreateSoldier"
    initial_description = (
        "When ON, a blank Soldier entry is automatically created for a user when they "
        "are granted the infantryboard.view_infantry_roster permission (i.e. join Infantry)."
    )
    default_value = True


@global_preference
class InfantryNewEntryPingEnabled(BoolChoicePreferenceDefinition):
    initial_name = "InfantryNewEntryPingEnabled"
    initial_description = (
        "When ON, a message is posted to InfantryLeadChannelID each time a new Soldier is added."
    )
    default_value = True


@global_preference
class InfantryAutoLinkOrgPlayer(BoolChoicePreferenceDefinition):
    initial_name = "InfantryAutoLinkOrgPlayer"
    initial_description = (
        "When OFF, new Soldier entries are NOT automatically linked to an OrgPlayer account."
    )
    default_value = True
