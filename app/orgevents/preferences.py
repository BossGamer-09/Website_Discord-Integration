from app.preferences.utils import global_preference
from app.preferences.types import DiscordChannelSelectPreferenceDefinition, IntPreferenceDefinition


@global_preference
class OrganicEventsMainChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "Organic Events Main Channel ID"
    initial_description = (
        "Text channel where organic event embeds are posted and threads are spawned. "
        "This is where the Organic Action Center panel should live."
    )
    channel_types_filter = ["text"]
    default_value = None


@global_preference
class OrganicEventRoleDeathwatch(IntPreferenceDefinition):
    initial_name = "Organic Events Role ID — Deathwatch"
    initial_description = "Discord role ID for the Deathwatch event type."
    default_value = 1457552799670997160


@global_preference
class OrganicEventRoleSCPvP(IntPreferenceDefinition):
    initial_name = "Organic Events Role ID — SC PvP"
    initial_description = "Discord role ID for the SC PvP event type."
    default_value = 1457552799574528068


@global_preference
class OrganicEventRoleLootGoblin(IntPreferenceDefinition):
    initial_name = "Organic Events Role ID — Loot Goblin"
    initial_description = "Discord role ID for the Loot Goblin event type."
    default_value = 1457552799670997159
