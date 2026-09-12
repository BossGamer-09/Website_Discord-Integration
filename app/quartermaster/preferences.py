from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    CharPreferenceDefinition,
    BoolChoicePreferenceDefinition,
    IntPreferenceDefinition,
)


@global_preference
class LootInventoryChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "LootInventoryChannelID"
    initial_description = "Channel where the live loot inventory embed is posted and kept updated."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class LootRequestChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "LootRequestChannelID"
    initial_description = (
        "Channel where loot request threads are created. "
        "QMs are added to each thread to approve/deny."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class LootQMGroupIDs(CharPreferenceDefinition):
    initial_name = "LootQMGroupIDs"
    initial_description = (
        "Comma-separated Django group PKs whose members are added to every loot request thread as QMs."
    )
    default_value = ""


@global_preference
class LootNotificationsEnabled(BoolChoicePreferenceDefinition):
    initial_name = "LootNotificationsEnabled"
    initial_description = "When ON, members receive a DM when their loot request is approved or denied."
    default_value = True


# ── Carried over from app.merits ──────────────────────────────────────────────

@global_preference
class MeritStaffChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MeritStaffChannelID"
    initial_description = "Channel where new merit/money requests are posted for staff review."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class MeritReminderDays(IntPreferenceDefinition):
    initial_name = "MeritReminderDays"
    initial_description = "Days after approval before the bot sends a reminder to staff to fulfill. 0 = disabled."
    default_value = 2


@global_preference
class MeritNotificationsEnabled(BoolChoicePreferenceDefinition):
    initial_name = "MeritNotificationsEnabled"
    initial_description = "When ON, the bot DMs requesters when their merit request is approved, fulfilled, or denied."
    default_value = True


@global_preference
class MeritFriendCheckUsername(CharPreferenceDefinition):
    initial_name = "MeritFriendCheckUsername"
    initial_description = "RSI username members must have on friends list before submitting. Blank = gate disabled."
    default_value = "discoDingo"


@global_preference
class MeritFriendCheckRSIUrl(CharPreferenceDefinition):
    initial_name = "MeritFriendCheckRSIUrl"
    initial_description = "Full RSI profile URL shown on the friends-list gate page."
    default_value = "https://robertsspaceindustries.com/en/citizens/discoDingo"


# ── Carried over from app.inventory ──────────────────────────────────────────

@global_preference
class StockLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "StockLogChannelID"
    initial_description = "Channel where QM stock actions (assign, return, lost, low-stock warnings) are logged."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class WeaponInventoryChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "WeaponInventoryChannelID"
    initial_description = "Channel where the automated crafted weapon inventory list is maintained."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class EquipmentChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "EquipmentChannelID"
    initial_description = "Channel where the live equipment panel embed is posted (members click Request here)."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class EquipmentRequestChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "EquipmentRequestChannelID"
    initial_description = "Channel where equipment request threads are created for QM review."
    default_value = 0
    channel_types_filter = ["text"]
