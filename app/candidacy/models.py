# app/candidacy/models.py
from functools import cached_property

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from simple_history.models import HistoricalRecords
from guardian.shortcuts import get_objects_for_user
from app.main.util.db import OriginalStateMixin, SignalEmittingManager
from django.conf import settings
from asgiref.sync import sync_to_async

from django.contrib.auth.models import Group


class SquireTrialThread(models.Model):
    """
    Persists the state of an active squire trial thread.
    """
    class Status(models.TextChoices):
        ACTIVE = 'ACTV', 'Active'
        SUCCESS = 'SUCC', 'Succeeded'
        DENIED = 'DENY', 'Denied'

    status = models.CharField(
        max_length=4,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    thread_id = models.BigIntegerField(primary_key=True, help_text="Discord Thread ID")
    squire_id = models.BigIntegerField(help_text="Discord User ID of the subject squire")
    initiator_id = models.BigIntegerField(null=True, blank=True, help_text="Discord User ID of the initiator")

    # Timestamps
    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(auto_now_add=True, null=True, blank=True, db_index=True)

    @property
    def is_active(self):
        return not bool(self.closed_at)

    class Meta:
        default_permissions = ()
        ordering = ['-started_at']
        permissions = [
            ("can_mod_squiretrialthread", "Can start/conclude a Squire Trial"),
            ("can_feedback_squiretrialthread", "Can give feedback on Squire Trial"),
        ]

    def __str__(self):
        return f"Squire Trial Thread - {self.thread_id} ({'Active' if self.is_active else 'Closed'})"


class ApplicationType(models.Model):
    """
    Defines an available application position or training program.
    """
    class Kind(models.TextChoices):
        STAFF = 'STAFF', 'Staff Position'
        TRAINING = 'TRNG', 'Training Program'

    kind = models.CharField(max_length=10, choices=Kind.choices, db_index=True, help_text="Is this a staff or training application?")
    name = models.CharField(max_length=100, unique=True, help_text="Name of the program/position")
    description = models.TextField(blank=True, help_text="Description and requirements")
    is_active = models.BooleanField(default=True, db_index=True, help_text="Whether this is currently active")
    on_accept_gives_permission_groups = models.ManyToManyField(Group, related_name="given_by_application_type", help_text="When accepted automatically give these permission groups to the user.")
    on_accept_disclaimer = models.TextField(blank=True, help_text="Text to give to a user when they are accepted.")

    # Up to 5 questions for the Discord Modal
    question_1 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 1 (Optional, if all questions are empty skips modal)")
    question_2 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 2 (Optional)")
    question_3 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 3 (Optional)")
    question_4 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 4 (Optional)")
    question_5 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 5 (Optional)")

    def clean(self):
        if not self.pk and ApplicationType.objects.filter(kind=self.kind).count() >= 25:
            raise ValidationError(f"Maximum of 25 entries allowed for kind {self.get_kind_display()}. (Discord Limitation)")
        super().clean()

    class Meta:
        pass

    def __str__(self):
        return f"[{self.get_kind_display()}] {self.name}"


class ApplicationRecord(models.Model):
    """
    Tracks generic applications for staff or training.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Review'
        APPROVED = 'APPROVED', 'Approved'
        DENIED = 'DENIED', 'Denied'

    applicant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='applications')
    app_type = models.ForeignKey(ApplicationType, on_delete=models.SET_NULL, null=True, related_name='records')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    thread_id = models.BigIntegerField(unique=True, help_text="Discord Thread ID for this application")

    @property
    def kind(self):
        return self.app_type.kind

    # Answers corresponding to the position's questions
    answer_1 = models.TextField(blank=True, null=True)
    answer_2 = models.TextField(blank=True, null=True)
    answer_3 = models.TextField(blank=True, null=True)
    answer_4 = models.TextField(blank=True, null=True)
    answer_5 = models.TextField(blank=True, null=True)

    reviewer = models.ForeignKey('unifieduser.OrgPlayer', on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_apps')
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ['-submitted_at']
        permissions = [
            ("can_review_application", "Can review applications"),
            ("can_submit_application", "Can submit applications")
        ]

    def __str__(self):
        return f"{self.applicant} - {self.app_type} ({self.status})"

    @sync_to_async
    def ahas_perm_for(self, perm: str, user):
        return self.has_perm_for(perm, user)

    def has_perm_for(self, perm: str, user):
        """
        Checks if a user has given permission on the model, also checks the equivalent type permission.
        """
        if "." not in perm:
            perm = f"{self._meta.app_label}.{perm}"

        # Check direct/global on record
        if user.has_perm(perm, self):
            return True

        # Check type permission (Global or Object)
        type_perm = f"{perm}_type"
        if self.app_type and user.has_perm(type_perm, self.app_type):
            return True

        return False

    @classmethod
    @sync_to_async
    def ainstances_with_perm_for(cls, perm: str, user, pk=True):
        return cls.instances_with_perm_for(perm, user, pk)

    @classmethod
    def instances_with_perm_for(cls, perm: str, user, pk=True):
        """
        Get all instances/pks where the user has given permission.
        """
        if "." in perm:
            app_label, codename = perm.split(".")
        else:
            app_label = cls._meta.app_label
            codename = perm
            perm = f"{app_label}.{codename}"

        type_perm = f"{app_label}.{codename}_type"

        # Global Checks
        if user.has_perm(perm) or user.has_perm(type_perm):
            qs = cls.objects.all()
            return qs.values_list('pk', flat=True) if pk else qs

        # Object Level Checks
        qs_records = get_objects_for_user(user, perm, klass=cls, accept_global_perms=False)

        # Types explicitly granted to user
        allowed_types = get_objects_for_user(user, type_perm, klass=ApplicationType, accept_global_perms=False)
        qs_via_types = cls.objects.filter(app_type__in=allowed_types)

        # Union
        qs = (qs_records | qs_via_types).distinct()

        return list(qs.values_list('pk', flat=True)) if pk else qs


# ---------------------------------------------------------------------------
# Membership Application System
#
# Follows the same ApplicationType / ApplicationRecord pattern above.
# MembershipApplicationType  — defines a membership path (Legion, Aux, etc.)
#                               editable entirely from Django admin.
# MembershipApplicationRecord — one submission per applicant per type.
#
# RSIVerification              — tracks bio-code verification attempts.
# ---------------------------------------------------------------------------

class MembershipApplicationTypeGroup(models.Model):
    """Explicit through model — avoids MySQL 64-char table-name truncation/hash mismatch."""
    membershipapplicationtype = models.ForeignKey(
        "MembershipApplicationType", on_delete=models.CASCADE
    )
    group = models.ForeignKey(Group, on_delete=models.CASCADE)

    class Meta:
        default_permissions = ()
        db_table = "candidacy_membershiptype_groups"
        unique_together = [("membershipapplicationtype", "group")]


class MembershipApplicationTypeEvalGroup(models.Model):
    """Through model for evaluation_permission_groups (Grant Evaluation button)."""
    membershipapplicationtype = models.ForeignKey(
        "MembershipApplicationType", on_delete=models.CASCADE
    )
    group = models.ForeignKey(Group, on_delete=models.CASCADE)

    class Meta:
        default_permissions = ()
        db_table = "candidacy_membershiptype_evalgroups"
        unique_together = [("membershipapplicationtype", "group")]


class MembershipApplicationType(models.Model):
    """
    Defines a membership path (e.g. Legion, Auxiliary).

    Admins create/edit these in Django admin. The verification portal cog
    reads active rows and builds the Discord UI from them — no code changes
    needed to add a new path.

    Approval flow:
      - on_accept_trial_rank_pk  → OrgRank PK set on the user (mirrors cmd_usersetup)
      - on_accept_gives_permission_groups → Django Groups added to the user
      - on_accept_disclaimer     → text posted in the thread on approval
      - welcome_message          → message posted to the welcome channel
    Placeholders: {applicant} {applicant_name} {rank_prefix} {rank_name} {discipline_channel}
    """

    name = models.CharField(max_length=100, unique=True, help_text="Display name shown in Discord (e.g. 'Legion', 'Auxiliary')")
    description = models.TextField(blank=True, help_text="Short description shown in the portal embed field")
    is_active = models.BooleanField(default=True, db_index=True, help_text="Inactive types are hidden from the portal")

    # --- Rank granted on approval ---
    on_accept_trial_rank_pk = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=(
            "PK of the OrgRank granted to the applicant on approval (trial rank). "
            "Find the PK on the OrgRank list in Django admin. Leave blank to skip rank assignment."
        ),
    )

    # --- Rank promoted to after trial (e.g. Trial → Legion Member) ---
    on_promote_rank_pk = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=(
            "PK of the OrgRank assigned by the **Legion** option on the Promote button "
            "(full member rank). Leave blank to omit the Legion option."
        ),
    )

    # --- Alternate promotion rank (Trial option on the Promote button) ---
    on_promote_trial_rank_pk = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=(
            "PK of the OrgRank assigned by the **Trial** option on the Promote button "
            "(intermediate / probationary rank). Leave blank to omit the Trial option."
        ),
    )

    # --- Permission groups granted on approval (same pattern as ApplicationType) ---
    on_accept_gives_permission_groups = models.ManyToManyField(
        Group,
        blank=True,
        through="MembershipApplicationTypeGroup",
        related_name="given_by_membership_type",
        help_text="Permission groups automatically added to the user on approval.",
    )

    # --- Evaluation groups offered via the 'Grant Evaluation' review button ---
    evaluation_permission_groups = models.ManyToManyField(
        Group,
        blank=True,
        through="MembershipApplicationTypeEvalGroup",
        related_name="eval_granted_by_membership_type",
        help_text=(
            "Groups shown in the 'Grant Evaluation' review button's selector "
            "(e.g. Blue Channel access for evaluating the applicant)."
        ),
    )

    # --- Text sent on approval ---
    on_accept_disclaimer = models.TextField(
        blank=True,
        help_text=(
            "Message posted to the application thread on approval. "
            "Supports: {applicant} {rank_prefix} {rank_name} {discipline_channel}"
        ),
        default=(
            "## ✅ Application Approved\n\n"
            "Welcome, {applicant}!\n\n"
            "You are now a **{rank_prefix} {rank_name}**.\n\n"
            "Head to {discipline_channel} and choose your discipline(s).\n\n"
            "*This thread will be archived in {archive_hours} hours.*"
        ),
    )
    welcome_message = models.TextField(
        blank=True,
        default=(
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
        ),
        help_text=(
            "Message posted to the welcome channel on approval. "
            "Supports: {applicant} {applicant_name} {rank_prefix} {rank_name} {discipline_channel}. "
            "Leave blank to use the global MembershipWelcomeMessage preference."
        ),
    )

    # --- Up to 5 modal questions (same pattern as ApplicationType) ---
    question_1 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 1 (if all blank, modal is skipped)")
    question_2 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 2 (Optional)")
    question_3 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 3 (Optional)")
    question_4 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 4 (Optional)")
    question_5 = models.CharField(max_length=45, blank=True, null=True, help_text="Question 5 (Optional)")

    AGE_QUESTION_CHOICES = [
        (0, "None — no age check"),
        (1, "Question 1"),
        (2, "Question 2"),
        (3, "Question 3"),
        (4, "Question 4"),
        (5, "Question 5"),
    ]
    age_question_index = models.PositiveSmallIntegerField(
        default=0,
        choices=AGE_QUESTION_CHOICES,
        help_text=(
            "Which question asks for the applicant's age? "
            "That answer will be validated as a whole number and compared against the minimum age preference. "
            "Set to 'None' to disable age checking for this application type."
        ),
    )

    def clean(self):
        active_count = MembershipApplicationType.objects.filter(is_active=True).exclude(pk=self.pk).count()
        if self.is_active and active_count >= 25:
            raise ValidationError("Maximum of 25 active membership types allowed. (Discord select menu limit)")
        super().clean()

    async def get_rank(self):
        """Return the OrgRank instance if on_accept_trial_rank_pk is set, else None."""
        if self.on_accept_trial_rank_pk:
            from app.unifieduser.models import OrgRank
            return await OrgRank.objects.filter(pk=self.on_accept_trial_rank_pk).afirst()
        return None

    class Meta:
        default_permissions = ()
        permissions = [
            ("can_review_membershipapplication", "Can review membership applications"),
        ]

    def __str__(self):
        return f"[{'Active' if self.is_active else 'Inactive'}] {self.name}"


class MembershipApplicationRecord(models.Model):
    """
    One membership application submission by one applicant.

    The Discord thread is locked + archived on close but NEVER deleted —
    staff can always view history.

    RSI verification state is tracked on the related RSIVerification rows;
    this record just caches the final handle for display.
    """
    class Status(models.TextChoices):
        PENDING  = 'PENDING',  'Pending Review'
        APPROVED = 'APPROVED', 'Approved'
        DENIED   = 'DENIED',   'Denied'
        RESET    = 'RESET',    'Reset'

    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='membership_applications',
    )
    membership_type = models.ForeignKey(
        MembershipApplicationType,
        on_delete=models.SET_NULL,
        null=True,
        related_name='records',
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    # Discord thread — locked + archived on close, never deleted
    thread_id = models.BigIntegerField(
        unique=True,
        help_text="Discord Thread Snowflake. Thread is locked/archived on close, never deleted.",
    )
    thread_archived  = models.BooleanField(default=False, help_text="Whether the thread has been auto-archived.")
    follow_up_sent   = models.BooleanField(default=False, help_text="Whether the post-approval follow-up DM has been sent.")
    flagged_suspicious = models.BooleanField(default=False, help_text="Flagged by staff as a suspicious applicant.")
    flag_reason      = models.TextField(blank=True, default="")

    # RSI (cached from RSIVerification on submission)
    rsi_handle = models.CharField(max_length=60, blank=True)
    rsi_profile_url = models.URLField(max_length=200, blank=True)
    rsi_verified = models.BooleanField(default=False)

    # Answers (mirrors ApplicationRecord pattern — up to 5)
    answer_1 = models.TextField(blank=True, null=True)
    answer_2 = models.TextField(blank=True, null=True)
    answer_3 = models.TextField(blank=True, null=True)
    answer_4 = models.TextField(blank=True, null=True)
    answer_5 = models.TextField(blank=True, null=True)

    # Age warning
    age_warning = models.BooleanField(default=False, help_text="True if applicant age < min_age")

    reviewer = models.ForeignKey(
        'unifieduser.OrgPlayer',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_membership_apps',
    )
    promoted_by = models.ForeignKey(
        'unifieduser.OrgPlayer',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='promoted_membership_apps',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    promoted_at  = models.DateTimeField(null=True, blank=True)
    reviewer_notes = models.TextField(blank=True)

    primary_focus = models.ForeignKey(
        'AssignableDiscipline',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='confirmed_focus_records',
        help_text="Primary focus (discipline) confirmed by the approving reviewer at approval time.",
    )

    # Recruiter / referrer — who brought this applicant in
    referred_by = models.ForeignKey(
        'unifieduser.OrgPlayer',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='referrals',
        help_text="Org member who referred / recruited this applicant.",
    )
    referral_note = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Free-text note about how the applicant found us (e.g. 'Reddit post', 'Friend of [name]').",
    )

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.applicant} → {self.membership_type} ({self.status})"


class MembershipEmbedMessage(models.Model):
    """
    Admin-configurable Discord embed posted to the welcome channel for a
    specific (membership_type, event) pair.

    Placeholders substituted at send time:
      {applicant}          — Discord mention of the user
      {applicant_name}     — Display name
      {rank_prefix}        — Granted OrgRank's prefix
      {rank_name}          — Granted OrgRank's name
      {discipline_channel} — Mention of the discipline selection channel
    """

    class Event(models.TextChoices):
        ACCEPT         = 'ACCEPT',         'On Acceptance'
        PROMOTE_TRIAL  = 'PROMOTE_TRIAL',  'On Promote → Trial'
        PROMOTE_LEGION = 'PROMOTE_LEGION', 'On Promote → Legion'

    membership_type = models.ForeignKey(
        MembershipApplicationType,
        on_delete=models.CASCADE,
        related_name='embed_messages',
    )
    event = models.CharField(max_length=20, choices=Event.choices, db_index=True)
    enabled = models.BooleanField(default=True)

    # Top-level message text (rendered above the embed — supports mentions)
    content = models.TextField(
        blank=True,
        help_text="Optional text message posted above the embed. Supports placeholders and mentions.",
    )

    # Embed body
    title = models.CharField(max_length=256, blank=True)
    description = models.TextField(blank=True, help_text="Main embed body. Supports placeholders.")
    color_hex = models.CharField(
        max_length=7,
        blank=True,
        default='#5865F2',
        help_text="Hex color like #5865F2. Leave blank for Discord blurple.",
    )

    # Media
    image_url = models.URLField(max_length=500, blank=True, help_text="Large image at the bottom of the embed.")
    thumbnail_url = models.URLField(max_length=500, blank=True, help_text="Small image on the top right.")
    include_welcome_card = models.BooleanField(
        default=False,
        help_text="Generate and attach the server welcome card (overrides image_url).",
    )

    # Author / Footer
    author_name = models.CharField(max_length=256, blank=True)
    author_icon_url = models.URLField(max_length=500, blank=True)
    footer_text = models.CharField(max_length=2048, blank=True)
    footer_icon_url = models.URLField(max_length=500, blank=True)

    class Meta:
        default_permissions = ()
        unique_together = [('membership_type', 'event')]
        verbose_name = "Membership Embed Message"
        verbose_name_plural = "Membership Embed Messages"

    def __str__(self):
        return f"{self.membership_type.name} — {self.get_event_display()}"


class RSIVerification(models.Model):
    """
    Tracks a single RSI bio-code verification attempt for a Discord user.
    Kept separate so verification state is independent of application state.
    """
    class Status(models.TextChoices):
        PENDING  = 'PENDING',  'Pending'
        VERIFIED = 'VERIFIED', 'Verified'
        EXPIRED  = 'EXPIRED',  'Expired'
        FAILED   = 'FAILED',   'Failed'

    # Discord snowflake — no FK, user may not yet be in backend at verify time
    discord_id = models.BigIntegerField(db_index=True, help_text="Discord User Snowflake")
    rsi_handle = models.CharField(max_length=60)
    rsi_profile_url = models.URLField(max_length=200)
    verification_code = models.CharField(max_length=20)
    verification_status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # New fields for RSI profile details
    enlisted_date = models.DateField(null=True, blank=True)
    org_membership = models.CharField(max_length=100, blank=True)

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ['-created_at']

    def __str__(self):
        return f"RSIVerification({self.discord_id}, {self.rsi_handle}, {self.verification_status})"


class MembershipRules(models.Model):
    """
    Singleton-style model holding the rules text shown to applicants.
    Split pages with a line containing only '---'. Staff edit this in Django admin.
    """
    rules_text = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Full rules text. Separate pages with a line containing only '---' (triple-dash). "
            "Applicants must click through every page before the agree button appears."
        ),
    )

    class Meta:
        default_permissions = ()
        verbose_name = "Membership Rules"
        verbose_name_plural = "Membership Rules"

    def __str__(self):
        return "Membership Rules"

    @classmethod
    def get_instance(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    async def aget_instance(cls):
        obj, _ = await cls.objects.aget_or_create(pk=1)
        return obj


class RulesAgreement(models.Model):
    """
    Records that a Discord user agreed to the rules at a specific rules version (hash).
    If the rules text changes the hash changes and the user must re-agree.
    """
    discord_id = models.BigIntegerField(unique=True, db_index=True)
    rules_hash = models.CharField(max_length=64)
    agreed_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        verbose_name = "Rules Agreement"
        verbose_name_plural = "Rules Agreements"

    def __str__(self):
        return f"RulesAgreement(discord_id={self.discord_id}, agreed_at={self.agreed_at:%Y-%m-%d})"


class AssignableDiscipline(models.Model):
    """
    A discipline that users can self-assign on Discord if they have permission.
    """
    name = models.CharField(max_length=100, unique=True)
    permission_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="assignable_disciplines",
        help_text="The Django permission groups to grant upon selection."
    )
    description = models.TextField(blank=True, help_text="Description shown in the Discord selection menu.")
    notification_channel_id = models.BigIntegerField(default=0, help_text="Discord Channel ID where a message is sent when a user joins.")
    notification_message = models.TextField(
        blank=True,
        default="{user} has joined the **{discipline}** discipline!",
        help_text="Message sent to the notification channel. Supports: {user} (mention), {name} (display name), {discipline} (discipline name)."
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ("can_self_assign_discipline", "Can assign this discipline to self"),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Onboarding leader rotation
# ---------------------------------------------------------------------------

class OnboardingLeaderAssignment(models.Model):
    """Weekly rotation of leaders assigned to the onboarding process."""

    week_start   = models.DateField(db_index=True, help_text="ISO week Monday date")
    leaders      = models.ManyToManyField(
        "unifieduser.OrgPlayer", blank=True, related_name="onboarding_assignments",
    )
    posted_message_id = models.BigIntegerField(null=True, blank=True, help_text="Discord message ID of the posted assignment")
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-week_start"]
        permissions = [
            ("manage_onboarding_assignments", "Can manage onboarding leader assignments"),
        ]

    def __str__(self):
        return f"Onboarding assignment w/c {self.week_start}"
