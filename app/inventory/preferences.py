from app.preferences.utils import global_preference
from app.preferences.types import DiscordChannelSelectPreferenceDefinition


@global_preference
class StockLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "StockLogChannelID"
    initial_description = (
        "Channel where quartermaster stock actions are logged "
        "(unit assigned to knight, unit returned, low-stock warnings)."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class WeaponInventoryChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "WeaponInventoryChannelID"
    initial_description = (
        "Channel where the automated weapon inventory list is maintained. "
        "The bot will clear its own old messages and repost the full list on every update."
    )
    default_value = 1488454183907627069
    channel_types_filter = ["text"]
