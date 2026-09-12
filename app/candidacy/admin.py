"""
app/candidacy/admin.py

Django admin registrations for the candidacy app.

Covers:
  - SquireTrialThread          (read-only audit view)
  - ApplicationType            (staff/training application config)
  - ApplicationRecord          (staff/training submissions — read-only review)
  - MembershipApplicationType  (membership path config — the main thing admins edit)
  - MembershipApplicationRecord (membership submissions — read-only review)
  - RSIVerification            (read-only audit view)
"""
from django.contrib import admin
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin

from app.main.admin import GranularObjPermAdmin, GranularObjPermAdminMixin

from .models import (
    SquireTrialThread,
    ApplicationType,
    ApplicationRecord,
    MembershipApplicationType,
    MembershipApplicationTypeGroup,
    MembershipApplicationTypeEvalGroup,
    MembershipApplicationRecord,
    MembershipEmbedMessage,
    RSIVerification,
    AssignableDiscipline,
    MembershipRules,
)


# ---------------------------------------------------------------------------
# SquireTrialThread  — audit only, no editing
# ---------------------------------------------------------------------------

@admin.register(SquireTrialThread)
class SquireTrialThreadAdmin(admin.ModelAdmin):
    list_display  = ("thread_id", "squire_id", "initiator_id", "status", "started_at", "closed_at", "is_active")
    list_filter   = ("status",)
    search_fields = ("thread_id", "squire_id", "initiator_id")
    ordering      = ("-started_at",)
    readonly_fields = ("thread_id", "squire_id", "initiator_id", "started_at", "closed_at", "status")

    def is_active(self, obj):
        return obj.is_active
    is_active.boolean = True
    is_active.short_description = "Active?"

    def has_add_permission(self, request):
        return False

# ---------------------------------------------------------------------------
# ApplicationType  (staff / training config)
# ---------------------------------------------------------------------------

@admin.register(ApplicationType)
class ApplicationTypeAdmin(admin.ModelAdmin):
    list_display  = ("name", "kind", "is_active", "question_count")
    list_filter   = ("kind", "is_active")
    search_fields = ("name",)
    ordering      = ("kind", "name")
    filter_horizontal = ("on_accept_gives_permission_groups",)

    fieldsets = (
        ("Identity", {
            "fields": ("kind", "name", "description", "is_active"),
        }),
        ("On Accept", {
            "description": "These actions fire automatically when an application is approved in Discord.",
            "fields": ("on_accept_gives_permission_groups", "on_accept_disclaimer"),
        }),
        ("Modal Questions", {
            "description": (
                "Up to 5 questions shown in the Discord application modal. "
                "Leave blank to skip the modal entirely."
            ),
            "fields": ("question_1", "question_2", "question_3", "question_4", "question_5"),
        }),
    )

    @admin.display(description="Questions")
    def question_count(self, obj):
        return sum(1 for i in range(1, 6) if getattr(obj, f"question_{i}"))


# ---------------------------------------------------------------------------
# ApplicationRecord  (staff / training submissions — read-only)
# ---------------------------------------------------------------------------

@admin.register(ApplicationRecord)
class ApplicationRecordAdmin(admin.ModelAdmin):
    list_display  = ("id", "applicant", "app_type", "status", "submitted_at", "reviewed_at", "reviewer", "thread_link")
    list_filter   = ("status", "app_type__kind", "app_type")
    search_fields = ("applicant__username", "app_type__name")
    ordering      = ("-submitted_at",)
    readonly_fields = (
        "applicant", "app_type", "thread_id",
        "answer_1", "answer_2", "answer_3", "answer_4", "answer_5",
        "submitted_at",
    )

    fieldsets = (
        ("Submission", {
            "fields": ("applicant", "app_type", "thread_id", "submitted_at"),
        }),
        ("Answers", {
            "fields": ("answer_1", "answer_2", "answer_3", "answer_4", "answer_5"),
        }),
        ("Review", {
            "fields": ("status", "reviewer", "reviewed_at"),
        }),
    )

    @admin.display(description="Thread")
    def thread_link(self, obj):
        if obj.thread_id:
            return format_html(
                '<a href="https://discord.com/channels/@me/{}" target="_blank">{}</a>',
                obj.thread_id, obj.thread_id,
            )
        return "—"

    def has_add_permission(self, request):
        return False


# ---------------------------------------------------------------------------
# MembershipApplicationType  — primary config surface for admins
# ---------------------------------------------------------------------------

class MembershipApplicationTypeGroupInline(admin.TabularInline):
    model = MembershipApplicationTypeGroup
    extra = 1
    verbose_name = "Permission Group"
    verbose_name_plural = "On Accept — Permission Groups"


class MembershipApplicationTypeEvalGroupInline(admin.TabularInline):
    model = MembershipApplicationTypeEvalGroup
    extra = 1
    verbose_name = "Evaluation Group"
    verbose_name_plural = "Evaluation — Offered Permission Groups (Grant Evaluation button)"


class MembershipEmbedMessageInline(admin.StackedInline):
    model = MembershipEmbedMessage
    extra = 0
    fk_name = "membership_type"
    classes = ("collapse",)
    verbose_name = "Embed Message"
    verbose_name_plural = "Embed Messages (per event — posted in the welcome channel)"
    fieldsets = (
        (None, {
            "fields": ("event", "enabled"),
            "description": (
                "Placeholders usable in any text field: "
                "{applicant} {applicant_name} {rank_prefix} {rank_name} {discipline_channel}."
            ),
        }),
        ("Message", {
            "fields": ("content", "title", "description", "color_hex"),
        }),
        ("Media", {
            "fields": ("image_url", "thumbnail_url", "include_welcome_card"),
        }),
        ("Author / Footer", {
            "classes": ("collapse",),
            "fields": ("author_name", "author_icon_url", "footer_text", "footer_icon_url"),
        }),
    )


@admin.register(MembershipApplicationType)
class MembershipApplicationTypeAdmin(admin.ModelAdmin):
    list_display  = ("name", "is_active", "on_accept_trial_rank_pk", "question_count", "group_count")
    list_filter   = ("is_active",)
    search_fields = ("name",)
    ordering      = ("name",)
    inlines       = [
        MembershipApplicationTypeGroupInline,
        MembershipApplicationTypeEvalGroupInline,
        MembershipEmbedMessageInline,
    ]

    fieldsets = (
        ("Identity", {
            "fields": ("name", "description", "is_active"),
        }),
        ("On Approval — Rank", {
            "description": (
                "Set the PKs of the OrgRanks to grant. "
                "Find PKs on the OrgRank list in Django admin (Unifieduser → Org Ranks).\n"
                "Trial rank pk (on_accept_trial_rank_pk): granted immediately on approval.\n"
                "Promote — Legion (on_promote_rank_pk): granted when staff picks 'Legion' from the Promote button selector.\n"
                "Promote — Trial (on_promote_trial_rank_pk): granted when staff picks 'Trial' from the Promote button selector.\n"
                "Leave both promote fields blank to hide the Promote button for this type."
            ),
            "fields": ("on_accept_trial_rank_pk", "on_promote_rank_pk", "on_promote_trial_rank_pk"),
        }),
        ("On Approval — Messages", {
            "description": (
                "Placeholders available in both fields:\n"
                "  {applicant}          — Discord mention of the approved user\n"
                "  {applicant_name}     — Display name of the approved user (no mention)\n"
                "  {rank_prefix}        — Prefix of the granted OrgRank (e.g. 'T')\n"
                "  {rank_name}          — Name of the granted OrgRank\n"
                "  {discipline_channel} — Mention of the discipline selection channel\n"
                "  {status}             — Approval status label"
            ),
            "fields": ("on_accept_disclaimer", "welcome_message"),
        }),
        ("Modal Questions", {
            "description": (
                "Up to 5 questions shown in the Discord application modal. "
                "Leave all blank to skip the modal entirely and submit immediately on selection. "
                "Use 'Age question' to designate which question asks for age — that answer will be "
                "validated as a whole number and flagged if under the minimum age."
            ),
            "fields": ("question_1", "question_2", "question_3", "question_4", "question_5", "age_question_index"),
        }),
    )

    @admin.display(description="Questions")
    def question_count(self, obj):
        return sum(1 for i in range(1, 6) if getattr(obj, f"question_{i}"))

    @admin.display(description="Groups on Accept")
    def group_count(self, obj):
        return obj.on_accept_gives_permission_groups.count()


# ---------------------------------------------------------------------------
# MembershipApplicationRecord  — read-only review surface
# ---------------------------------------------------------------------------

@admin.register(MembershipApplicationRecord)
class MembershipApplicationRecordAdmin(SimpleHistoryAdmin):
    list_display  = (
        "id", "applicant", "membership_type", "status", "primary_focus",
        "rsi_handle", "rsi_verified", "submitted_at", "reviewed_at", "reviewer",
        "promoted_at", "promoted_by", "thread_link",
    )
    list_filter   = ("status", "membership_type", "primary_focus", "rsi_verified")
    search_fields = ("applicant__username", "rsi_handle", "membership_type__name")
    ordering      = ("-submitted_at",)
    readonly_fields = (
        "applicant", "membership_type", "thread_id",
        "rsi_handle", "rsi_profile_url", "rsi_verified",
        "answer_1", "answer_2", "answer_3", "answer_4", "answer_5",
        "submitted_at", "promoted_at", "promoted_by",
    )

    fieldsets = (
        ("Submission", {
            "fields": ("applicant", "membership_type", "thread_id", "submitted_at"),
        }),
        ("RSI Verification", {
            "fields": ("rsi_handle", "rsi_profile_url", "rsi_verified"),
        }),
        ("Answers", {
            "fields": ("answer_1", "answer_2", "answer_3", "answer_4", "answer_5"),
        }),
        ("Review", {
            "fields": ("status", "reviewer", "reviewed_at", "reviewer_notes", "primary_focus"),
        }),
        ("Promotion", {
            "fields": ("promoted_by", "promoted_at"),
        }),
    )

    @admin.display(description="Thread")
    def thread_link(self, obj):
        if obj.thread_id:
            return format_html(
                '<a href="https://discord.com/channels/@me/{}" target="_blank">{}</a>',
                obj.thread_id, obj.thread_id,
            )
        return "—"

    def has_add_permission(self, request):
        return False


# ---------------------------------------------------------------------------
# RSIVerification  — read-only audit view
# ---------------------------------------------------------------------------

@admin.register(RSIVerification)
class RSIVerificationAdmin(SimpleHistoryAdmin):
    list_display  = ("id", "discord_id", "rsi_handle", "verification_status", "created_at", "expires_at", "verified_at")
    list_filter   = ("verification_status",)
    search_fields = ("discord_id", "rsi_handle")
    ordering      = ("-created_at",)
    readonly_fields = (
        "discord_id", "rsi_handle", "rsi_profile_url",
        "verification_code", "verification_status",
        "expires_at", "verified_at", "created_at",
    )

    fieldsets = (
        ("Discord User", {
            "fields": ("discord_id", "rsi_handle", "rsi_profile_url"),
        }),
        ("Verification", {
            "fields": ("verification_code", "verification_status", "expires_at", "verified_at", "created_at"),
        }),
    )

    def has_add_permission(self, request):
        return False


# dicipline selection

@admin.register(AssignableDiscipline)
class AssignableDisciplineAdmin(GranularObjPermAdmin):
    list_display = ("name", "get_groups_count", "is_active", "notification_channel_id")
    list_filter = ("is_active",)
    search_fields = ("name", "permission_groups__name")
    ordering = ("name",)
    filter_horizontal = ("permission_groups",)

    @admin.display(description="Groups")
    def get_groups_count(self, obj):
        return obj.permission_groups.count()


@admin.register(MembershipEmbedMessage)
class MembershipEmbedMessageAdmin(admin.ModelAdmin):
    list_display = ("membership_type", "event", "enabled", "title")
    list_filter = ("event", "enabled", "membership_type")
    search_fields = ("membership_type__name", "title", "description")
    ordering = ("membership_type__name", "event")

    fieldsets = (
        (None, {
            "fields": ("membership_type", "event", "enabled"),
            "description": (
                "Placeholders: "
                "{applicant} {applicant_name} {rank_prefix} {rank_name} {discipline_channel}."
            ),
        }),
        ("Message", {
            "fields": ("content", "title", "description", "color_hex"),
        }),
        ("Media", {
            "fields": ("image_url", "thumbnail_url", "include_welcome_card"),
        }),
        ("Author / Footer", {
            "classes": ("collapse",),
            "fields": ("author_name", "author_icon_url", "footer_text", "footer_icon_url"),
        }),
    )


@admin.register(MembershipRules)
class MembershipRulesAdmin(admin.ModelAdmin):
    """Single-record admin for editing the membership rules text."""

    def has_add_permission(self, request):
        return not MembershipRules.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    fieldsets = (
        (None, {
            "fields": ("rules_text",),
            "description": (
                "Paste the full rules here. "
                "Separate pages with a line containing only <code>---</code> (three dashes, nothing else on that line). "
                "Applicants must click through every page before the agree button appears."
            ),
        }),
    )
