from app.preferences.utils import global_preference
from app.preferences.types import (
    DiscordChannelSelectPreferenceDefinition,
    BoolChoicePreferenceDefinition,
    IntPreferenceDefinition,
    CharPreferenceDefinition,
    PermissionGroupSelectPreferenceDefinition,
)


@global_preference
class LeadershipLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = 'LeadershipLogChannelID'
    initial_description = 'Channel where leadership actions (bans, kicks, mutes, notes) are logged'
    default_value = 0
    channel_types_filter = ['text']


@global_preference
class LeadershipApprovalChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = 'LeadershipApprovalChannelID'
    initial_description = 'Private channel where actions requiring approval (Thunderlake/Dolby/Mars ranks) are posted'
    default_value = 0
    channel_types_filter = ['text']


@global_preference
class LeadershipDecisionLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = 'LeadershipDecisionLogChannelID'
    initial_description = 'Channel for the full decision log (promotions, demotions, role assignments, staff actions)'
    default_value = 0
    channel_types_filter = ['text']


@global_preference
class LeadershipApprovalRequiredRankPKs(CharPreferenceDefinition):
    initial_name = 'LeadershipApprovalRequiredRankPKs'
    initial_description = (
        'Comma-separated OrgRank PKs that require approval chain before ban/kick/blacklist '
        '(e.g. Thunderlake, Dolby, Mars equivalents). Leave blank to disable approval gating.'
    )
    default_value = ''


@global_preference
class LeadershipInactiveRankPK(IntPreferenceDefinition):
    initial_name = 'LeadershipInactiveRankPK'
    initial_description = 'OrgRank PK assigned when a member is marked inactive (0 = skip rank change)'
    default_value = 0


@global_preference
class LeadershipAnnounceBanEnabled(BoolChoicePreferenceDefinition):
    initial_name = 'LeadershipAnnounceBanEnabled'
    initial_description = 'Whether to post a public announcement when a ban is issued'
    default_value = False
