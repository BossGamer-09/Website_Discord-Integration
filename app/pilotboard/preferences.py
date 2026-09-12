"""
app/pilotboard/preferences.py

Runtime configuration for the pilot roster.
No hardcoded channel/guild IDs — all live here.
"""
from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    BoolChoicePreferenceDefinition,
)


@global_preference
class PilotRosterChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Pilot Roster Channel ID"
    initial_description = (
        "Text channel where pilot roster update notifications are posted "
        "(e.g. new pilots added, goals completed)."
    )


@global_preference
class PilotRosterPublicUpdates(BoolChoicePreferenceDefinition):
    initial_name = "Pilot Roster Public Updates"
    initial_description = (
        "When enabled, goal completions and new-pilot additions are announced "
        "in the Pilot Roster Channel."
    )
    default_value = False


@global_preference
class PilotLeadChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "PilotLeadChannelID"
    initial_description = (
        "Channel where Pilot Lead is pinged when a new Pilot entry is created. "
        "Set to 0 to disable."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class PilotAutoLinkOrgPlayer(BoolChoicePreferenceDefinition):
    initial_name = "PilotAutoLinkOrgPlayer"
    initial_description = (
        "When OFF, new Pilot entries are NOT automatically linked to an OrgPlayer account, "
        "and no scorecard is auto-created for pilots."
    )
    default_value = True


@global_preference
class PilotNewEntryPingEnabled(BoolChoicePreferenceDefinition):
    initial_name = "PilotNewEntryPingEnabled"
    initial_description = (
        "When ON, a message is posted to PilotLeadChannelID each time a new Pilot is added."
    )
    default_value = True
