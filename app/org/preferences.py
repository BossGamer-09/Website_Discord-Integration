from app.preferences.utils import global_preference
from app.preferences.types import DiscordChannelSelectPreferenceDefinition, BoolChoicePreferenceDefinition


@global_preference
class OrgAnnounceDefaultChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "OrgAnnounceDefaultChannelID"
    initial_description = "Default channel where approved org announcements are posted."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class OrgAnnounceApprovalChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "OrgAnnounceApprovalChannelID"
    initial_description = "Private channel where org announcement requests are posted for leader approval."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class OrgAnnounceRequireApproval(BoolChoicePreferenceDefinition):
    initial_name = "OrgAnnounceRequireApproval"
    initial_description = "When ON, announcements require leader approval before posting."
    default_value = True


@global_preference
class OrgAnnounceAuditLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "OrgAnnounceAuditLogChannelID"
    initial_description = "Channel where announcement approve/deny decisions are logged. Leave 0 to disable."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class PrivNotifyParentChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "PrivNotifyParentChannelID"
    initial_description = "Parent channel where private notification threads are created."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class ActivityPingPortalChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "ActivityPingPortalChannelID"
    initial_description = "Channel where the activity ping role self-assignment portal is posted."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class NicknameChangeLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "NicknameChangeLogChannelID"
    initial_description = "Channel where nickname change requests and approvals are logged. Leave 0 to disable."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class NicknameChangeReviewChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "NicknameChangeReviewChannelID"
    initial_description = "Staff channel where nickname change requests are posted for review."
    default_value = 0
    channel_types_filter = ["text"]
