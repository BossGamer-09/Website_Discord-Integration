from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from app.main.util.db import DBAwareModelMixin


class DisciplinaryRecord(DBAwareModelMixin, models.Model):
    """
    Tracks warnings, mutes, bans, kicks, and other disciplinary actions against a member.
    Central audit trail for leadership commands (Item 3).
    """

    class ActionType(models.TextChoices):
        WARN        = 'WARN',       'Warning'
        MUTE        = 'MUTE',       'Mute / Silence'
        KICK        = 'KICK',       'Kick'
        BAN         = 'BAN',        'Ban'
        BLACKLIST   = 'BLACKLIST',  'Blacklist'
        INACTIVE    = 'INACTIVE',   'Mark Inactive'
        REPRIMAND   = 'REPRIMAND',  'Formal Reprimand'
        NOTE        = 'NOTE',       'Staff Note'
        FORCE_ROLE  = 'FORCEROL',   'Force Role Change'

    class Status(models.TextChoices):
        ACTIVE   = 'ACTIVE',   'Active'
        PARDONED = 'PARDONED', 'Pardoned / Lifted'
        EXPIRED  = 'EXPIRED',  'Expired'

    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='disciplinary_records',
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='disciplinary_issued',
    )

    action_type = models.CharField(max_length=10, choices=ActionType.choices, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)

    reason = models.TextField()
    internal_notes = models.TextField(blank=True)

    # For timed mutes/bans
    expires_at = models.DateTimeField(null=True, blank=True, help_text="If set, action auto-lifts at this time.")

    # Approval chain: some actions (e.g. bans for Thunderlake/Dolby/Mars) require an approver
    requires_approval = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='disciplinary_approvals',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    # For FORCE_ROLE: store what role was forced
    forced_rank_id = models.PositiveIntegerField(null=True, blank=True, help_text="OrgRank PK set via force role change.")

    issued_at = models.DateTimeField(auto_now_add=True)
    pardoned_at = models.DateTimeField(null=True, blank=True)
    pardoned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='disciplinary_pardons',
    )

    # Announcement channel/thread for this action
    discord_thread_id = models.BigIntegerField(null=True, blank=True, help_text="Discord thread ID for this action, if created.")

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ['-issued_at']
        indexes = [
            models.Index(fields=['subject', 'action_type', 'status']),
            models.Index(fields=['issued_at']),
        ]
        permissions = [
            ('can_issue_warn',         'Can issue warnings'),
            ('can_issue_mute',         'Can mute/silence members'),
            ('can_issue_kick',         'Can kick members'),
            ('can_issue_ban',          'Can ban members'),
            ('can_issue_blacklist',    'Can blacklist members'),
            ('can_mark_inactive',      'Can mark members inactive'),
            ('can_issue_reprimand',    'Can issue formal reprimands'),
            ('can_add_staff_note',     'Can add staff notes to members'),
            ('can_force_role_change',  'Can force a rank/role change'),
            ('can_approve_discipline', 'Can approve pending disciplinary actions'),
            ('can_view_records',       'Can view disciplinary records'),
            ('can_promote',            'Can promote members to a higher rank'),
            ('can_demote',             'Can demote members to a lower rank'),
        ]

    def __str__(self):
        return f"[{self.get_action_type_display()}] {self.subject} by {self.issued_by} @ {self.issued_at:%Y-%m-%d}"

    @property
    def is_active(self):
        if self.status != self.Status.ACTIVE:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True


class DecisionLog(models.Model):
    """
    Immutable audit log for all leadership decisions (promotions, demotions,
    role assignments, annotations). Separate from DisciplinaryRecord — covers
    positive and procedural actions, not just disciplinary ones.
    """

    class EventType(models.TextChoices):
        PROMOTION         = 'PROMOTION',   'Promotion'
        DEMOTION          = 'DEMOTION',    'Demotion'
        ROLE_ASSIGN       = 'ROLE_ASSIGN', 'Role Assignment'
        ROLE_REMOVE       = 'ROLE_REMOVE', 'Role Removal'
        DISCIPLINE        = 'DISCIPLINE',  'Disciplinary Action'
        ONBOARD_DECISION  = 'ONBOARD',     'Onboarding Decision'
        STAFF_ACTION      = 'STAFF',       'Staff Action / Note'
        NOMINATION_AWARD  = 'NOMINATION',  'Nomination Awarded'

    event_type = models.CharField(max_length=20, choices=EventType.choices, db_index=True)

    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='decision_log_subject',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='decision_log_actor',
    )

    summary = models.CharField(max_length=500)
    detail = models.TextField(blank=True)

    # Optional FK to the disciplinary record it was generated from
    disciplinary_record = models.ForeignKey(
        DisciplinaryRecord,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='decision_logs',
    )

    # Snapshot of rank before/after for promotions/demotions
    rank_before_id = models.PositiveIntegerField(null=True, blank=True)
    rank_after_id = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        default_permissions = ()
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['subject', 'event_type']),
        ]
        permissions = [
            ('can_view_decision_log', 'Can view decision log'),
        ]

    def __str__(self):
        return f"[{self.get_event_type_display()}] {self.subject} — {self.summary[:60]}"
