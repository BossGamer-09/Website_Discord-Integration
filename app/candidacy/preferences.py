from django import forms
from django.conf import settings
from django.apps import apps

from app.preferences.utils import global_preference
from app.preferences.types import BoolChoicePreferenceDefinition, IntChoicePreferenceDefinition, CharPreferenceDefinition, IntPreferenceDefinition, StrChoicePreferenceDefinition, DiscordChannelSelectPreferenceDefinition


@global_preference
class SquirifyDiscordChanID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "SquirifyDiscordChanID"
    initial_description = "Squirify Parent channel"
    default_value = 1457552811742072885
    channel_types_filter = ["text"]


@global_preference
class SquirifyKnightRankPK(IntChoicePreferenceDefinition):
    initial_name = "SquirifyKnightRankPK"
    initial_description = (
        "PK of the OrgRank automatically assigned when a Squire Trial is accepted. "
        "Find the PK on the OrgRank list in Django admin. Set to 0 to skip auto-promotion."
    )
    default_value = 0

    @classmethod
    def get_choices(cls):
        OrgRank = apps.get_model('unifieduser', 'OrgRank')
        return [[rank.pk, "{} {}".format(rank.prefix, rank.name)] for rank in OrgRank.objects.all()]


@global_preference
class StaffApplicationsChanID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "StaffApplicationsChanID"
    initial_description = "Channel for Staff Applications"
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class TrainingApplicationsChanID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "TrainingApplicationsChanID"
    initial_description = "Channel for Training Applications"
    default_value = 0
    channel_types_filter = ["text"]


# ---------------------------------------------------------------------------
# Membership Application System
# ---------------------------------------------------------------------------

@global_preference
class MembershipVerificationChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MembershipVerificationChannelID"
    initial_description = (
        "Channel where the 'Start Verification' portal button is posted. "
        "The bot will clear its own old messages and repost on startup."
    )
    default_value = 1457552800799133723
    channel_types_filter = ["text"]


@global_preference
class MembershipReviewChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MembershipReviewChannelID"
    initial_description = "Private channel where new membership applications are posted for staff review"
    default_value = 1457552808671973488
    channel_types_filter = ["text"]


@global_preference
class MembershipApplicationChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MembershipApplicationChannelID"
    initial_description = "Public channel where new membership applications are announced"
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class MembershipTranscriptChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MembershipTranscriptChannelID"
    initial_description = "Channel where closed application threads are logged (transcripts)"
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class MembershipWelcomeChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MembershipWelcomeChannelID"
    initial_description = "Channel where approved members receive a welcome ping"
    default_value = 1457552800799133719
    channel_types_filter = ["text"]


@global_preference
class MembershipDisciplineSelectionChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MembershipDisciplineSelectionChannelID"
    initial_description = "Channel linked as {discipline_channel} in approval messages"
    default_value = 1457552800799133720
    channel_types_filter = ["text"]


@global_preference
class MembershipVisitorRankPK(IntChoicePreferenceDefinition):
    initial_name = "MembershipVisitorRankPK"
    initial_description = (
        "PK of the OrgRank granted when a user clicks 'Start Verification' (Visitor rank). "
        "Find the PK on the OrgRank list in Django admin. Set to 0 to skip."
    )
    default_value = 0

    @classmethod
    def get_choices(cls):
        OrgRank = apps.get_model('unifieduser', 'OrgRank')
        return [[rank.pk, "{} {}".format(rank.prefix, rank.name)] for rank in OrgRank.objects.all()]


@global_preference
class MembershipRSILinkingEnabled(BoolChoicePreferenceDefinition):
    initial_name = "MembershipRSILinkingEnabled"
    initial_description = (
        "Master switch — when OFF the entire RSI profile step is skipped: "
        "no URL collection, no bio-code, no RSI data stored on the application record."
    )
    default_value = True


@global_preference
class MembershipRSIVerificationEnabled(BoolChoicePreferenceDefinition):
    initial_name = "MembershipRSIVerificationEnabled"
    initial_description = (
        "Require bio-code verification of the RSI profile URL. "
        "Only applies when MembershipRSILinkingEnabled is ON."
    )
    default_value = True


@global_preference
class MembershipMinimumAge(IntPreferenceDefinition):
    initial_name = "MembershipMinimumAge"
    initial_description = "Minimum age applicants must declare in the 'age' question (0 = no check)"
    default_value = 18


@global_preference
class MembershipRSIVerificationTimeoutMinutes(IntPreferenceDefinition):
    initial_name = "MembershipRSIVerificationTimeoutMinutes"
    initial_description = "How many minutes an RSI bio-code verification attempt stays active before expiring"
    default_value = 30


@global_preference
class MembershipRSICheckIntervalSeconds(IntPreferenceDefinition):
    initial_name = "MembershipRSICheckIntervalSeconds"
    initial_description = "How often (seconds) the bot polls an RSI profile page for the bio-code"
    default_value = 20


@global_preference
class MembershipVisitorPermissionGroups(CharPreferenceDefinition):
    initial_name = "MembershipVisitorPermissionGroups"
    initial_description = "Comma-separated list of permission group IDs to grant to visitors (in addition to the rank)."
    default_value = ""


@global_preference
class MembershipApprovalMessage(CharPreferenceDefinition):
    initial_name = "MembershipApprovalMessage"
    initial_description = "Message posted in the public application channel when an application is approved"
    default_value = "✅ {applicant} has been approved as **{rank_name}**! Welcome!"


@global_preference
class MembershipDenialMessage(CharPreferenceDefinition):
    initial_name = "MembershipDenialMessage"
    initial_description = "Message posted in the public application channel when an application is denied"
    default_value = "❌ {applicant}'s application has been denied."


@global_preference
class MembershipThreadViewerGroups(CharPreferenceDefinition):
    initial_name = "MembershipThreadViewerGroups"
    initial_description = "Comma-separated list of Django group IDs whose members will be added to every application thread."
    default_value = ""

@global_preference
class MembershipWelcomeMessage(CharPreferenceDefinition):
    initial_name = "MembershipWelcomeMessage"
    initial_description = (
        "Message posted to the welcome channel when a membership application is approved. "
        "Available tokens: {applicant} (mention), {applicant_name} (display name), "
        "{rank_name}, {rank_prefix}, {discipline_channel}."
    )
    default_value = (
        "**Induction: BlightVeil Legion**\n\n"
        "{applicant} is no longer who they were.\n\n"
        "Today, {applicant_name} enters the Legion.\n\n"
        "In BlightVeil, we mark the day you commit to something greater than yourself.\n\n"
        "This is the point where excuses die\n"
        "and performance takes their place.\n\n"
        "From this point forward:\n"
        "Fight with purpose.\n"
        "Commit to improvement.\n"
        "Win.\n\n"
        "Welcome to the Legion."
    )


@global_preference
class MembershipInductionImageEnabled(BoolChoicePreferenceDefinition):
    initial_name = "MembershipInductionImageEnabled"
    initial_description = "Whether to generate and attach a welcome card image when posting the induction message."
    default_value = True


@global_preference
class MembershipInductionImagePath(CharPreferenceDefinition):
    initial_name = "MembershipInductionImagePath"
    initial_description = (
        "Absolute path to the background image used for the induction welcome card. "
        "Leave blank to use the default welcome_background.png."
    )
    default_value = ""

@global_preference
class MembershipDenialCooldownDays(IntPreferenceDefinition):
    initial_name = "MembershipDenialCooldownDays"
    initial_description = "Number of days a denied applicant must wait before reapplying (0 = no cooldown)"
    default_value = 30


@global_preference
class MembershipRulesEnabled(BoolChoicePreferenceDefinition):
    initial_name = "MembershipRulesEnabled"
    initial_description = "Require applicants to scroll through and acknowledge the rules before the application flow begins"
    default_value = False


@global_preference
class MembershipAutoArchiveHours(IntPreferenceDefinition):
    initial_name = "MembershipAutoArchiveHours"
    initial_description = "Number of hours after approval to lock/archive the thread."
    default_value = 24

@global_preference
class DisciplineSelectionChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "DisciplineSelectionChannelID"
    initial_description = (
        "Channel where the 'Join a Discipline' portal button is posted. "
        "The bot will clear its own old messages and repost on startup."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class DisciplineLogChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "DisciplineLogChannelID"
    initial_description = "Channel where discipline assignments and changes are logged. Leave 0 to disable."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class ChamberlainNotifyChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "ChamberlainNotifyChannelID"
    initial_description = (
        "Channel where Chamberlains are notified when an active applicant joins or leaves an event."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class ChamberlainApplicantEventTrackingEnabled(BoolChoicePreferenceDefinition):
    initial_name = "ChamberlainApplicantEventTrackingEnabled"
    initial_description = (
        "When ON, Chamberlains are pinged when an active applicant joins or leaves a scheduled event VC."
    )
    default_value = True


@global_preference
class OnboardingLeaderRotationEnabled(BoolChoicePreferenceDefinition):
    initial_name = "OnboardingLeaderRotationEnabled"
    initial_description = (
        "When ON, a weekly Celery task automatically rotates which leaders are assigned to onboarding."
    )
    default_value = False


@global_preference
class OnboardingLeaderChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "OnboardingLeaderChannelID"
    initial_description = (
        "Channel where the weekly onboarding leader assignment is posted."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class OnboardingLeaderGroupPKs(CharPreferenceDefinition):
    initial_name = "OnboardingLeaderGroupPKs"
    initial_description = (
        "Comma-separated PKs of Django groups whose members are eligible for weekly onboarding rotation."
    )
    default_value = ""


@global_preference
class MembershipDisciplineReminderDays(IntPreferenceDefinition):
    initial_name = "MembershipDisciplineReminderDays"
    initial_description = (
        "Days after approval to DM a member who still has no discipline selected. "
        "Set to 0 to disable."
    )
    default_value = 3


@global_preference
class MembershipFollowUpReminderDays(IntPreferenceDefinition):
    initial_name = "MembershipFollowUpReminderDays"
    initial_description = (
        "Days after approval to DM the new member a follow-up questions reminder. "
        "Set to 0 to disable."
    )
    default_value = 2


@global_preference
class MembershipAutoCloseDays(IntPreferenceDefinition):
    initial_name = "MembershipAutoCloseDays"
    initial_description = (
        "Days after which a PENDING application with no activity is auto-denied and thread locked. "
        "Set to 0 to disable."
    )
    default_value = 14


@global_preference
class MembershipSuspiciousUserChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "MembershipSuspiciousUserChannelID"
    initial_description = (
        "Staff channel where suspicious-user flag alerts are posted. "
        "Leave at 0 to fall back to MembershipReviewChannelID."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class MembershipFollowUpMessage(CharPreferenceDefinition):
    initial_name = "MembershipFollowUpMessage"
    initial_description = (
        "DM sent to newly-approved members as a follow-up reminder. "
        "Available tokens: {member} (mention), {member_name}."
    )
    default_value = (
        "Hey {member_name}! 👋\n\n"
        "Just checking in — make sure you've:\n"
        "• Selected your disciplines in the discipline channel\n"
        "• Reviewed the org rules\n"
        "• Introduced yourself to the team\n\n"
        "If you have any questions, don't hesitate to ask a staff member!"
    )


@global_preference
class RankAnnouncementChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "RankAnnouncementChannelID"
    initial_description = "Channel where promotion and demotion announcements are posted."
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class DisciplinePortalText(CharPreferenceDefinition):
    initial_name = "DisciplinePortalText"
    initial_description = "The header text shown on the specialized disciplines portal."
    default_value = (
        "# **__`Gameplay Interests`__**\n"
        "Our organization offers various specialized paths. Joining an Interest indicates this with a role and will get you pinged for this gameplay more, "
        "and is your 1st step to joining unique training programs, and specialized operations."
    )


# ---------------------------------------------------------------------------
# Join routing — "What brings you to BlightVeil?" dropdown
# ---------------------------------------------------------------------------

@global_preference
class JoinRoutingEnabled(BoolChoicePreferenceDefinition):
    initial_name = "JoinRoutingEnabled"
    initial_description = (
        "When ON, a dropdown is sent to new members asking 'What brings you to BlightVeil?' "
        "and routes them to the Applicant, External, or Visitor track automatically."
    )
    default_value = True


@global_preference
class JoinRoutingDropdownMessage(CharPreferenceDefinition):
    initial_name = "JoinRoutingDropdownMessage"
    initial_description = (
        "Message sent to the welcome channel with the routing dropdown. "
        "Supports: {member} (mention)."
    )
    default_value = (
        "Hey {member}! 👋 Welcome to **BlightVeil**.\n\n"
        "To get you to the right place, please tell us what brings you here:"
    )


@global_preference
class ExternalReviewChannelID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "ExternalReviewChannelID"
    initial_description = (
        "Channel where External Citizen applications are posted as threads for staff review. "
        "Should be a private text channel."
    )
    default_value = 0
    channel_types_filter = ["text"]


@global_preference
class ExternalThreadViewerGroups(CharPreferenceDefinition):
    initial_name = "ExternalThreadViewerGroups"
    initial_description = (
        "Comma-separated PKs of Django groups whose members are added to every External Citizen thread "
        "(same pattern as MembershipThreadViewerGroups)."
    )
    default_value = ""


@global_preference
class ExternalRankPK(IntChoicePreferenceDefinition):
    initial_name = "ExternalRankPK"
    initial_description = (
        "PK of the OrgRank granted to users who select 'I'm from another organization' "
        "on join. Set to 0 to skip rank assignment."
    )
    default_value = 0

    @classmethod
    def get_choices(cls):
        OrgRank = apps.get_model('unifieduser', 'OrgRank')
        return [[rank.pk, "{} {}".format(rank.prefix, rank.name)] for rank in OrgRank.objects.all()]


@global_preference
class ExternalPermissionGroups(CharPreferenceDefinition):
    initial_name = "ExternalPermissionGroups"
    initial_description = "Comma-separated list of permission group IDs granted to External users on join."
    default_value = ""


@global_preference
class VisitorRoutingRankPK(IntChoicePreferenceDefinition):
    initial_name = "VisitorRoutingRankPK"
    initial_description = (
        "PK of the OrgRank granted to users who select 'Just checking things out' on join. "
        "Mirrors MembershipVisitorRankPK — set either. Set to 0 to use MembershipVisitorRankPK."
    )
    default_value = 0

    @classmethod
    def get_choices(cls):
        OrgRank = apps.get_model('unifieduser', 'OrgRank')
        return [[rank.pk, "{} {}".format(rank.prefix, rank.name)] for rank in OrgRank.objects.all()]


@global_preference
class SuspiciousAccountAgeDays(IntPreferenceDefinition):
    initial_name = "SuspiciousAccountAgeDays"
    initial_description = (
        "Discord accounts younger than this many days trigger a suspicious-user alert "
        "to MembershipSuspiciousUserChannelID on join. Set to 0 to disable."
    )
    default_value = 7


@global_preference
class SuspiciousNoAvatarAlert(BoolChoicePreferenceDefinition):
    initial_name = "SuspiciousNoAvatarAlert"
    initial_description = (
        "When ON, users with no custom Discord avatar also trigger the suspicious-user alert."
    )
    default_value = True


# ---------------------------------------------------------------------------
# Applicant self-service commands
# ---------------------------------------------------------------------------

@global_preference
class ApplicantSelfServiceGroups(CharPreferenceDefinition):
    initial_name = "ApplicantSelfServiceGroups"
    initial_description = (
        "Comma-separated PKs of Django Groups (linked to Discord roles) whose members can use "
        "applicant self-service commands (/my_application, /reopen_application, /request_help, /my_discipline). "
        "Leave blank to allow anyone in the guild with a linked OrgPlayer account."
    )
    default_value = ""
