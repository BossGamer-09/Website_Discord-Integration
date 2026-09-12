"""
app/disfunction/models.py

All voice-channel and attendance models live here.
Tables are pinned to their original orm_models_* names so no data migration is needed.
"""
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from app.main.util.db import SignalEmittingManager, OriginalStateMixin


# ---------------------------------------------------------------------------
# Voice Channel Profiles — drives what type of VC each member can create
# ---------------------------------------------------------------------------

class VoiceChannelProfile(models.Model):
    """
    Defines a type of temporary voice channel that can be spawned.

    Permission check: the cog calls
        member.has_perm("disfunction.<required_permission>")
    Django groups (mapped from Discord roles via org.DiscordRole) grant these.
    Profiles are evaluated in ascending sort_order; the first match wins.
    """

    class ChannelClass(models.TextChoices):
        PUBLIC    = 'PUBLIC',    'Public'
        PROTECTED = 'PROTECTED', 'Protected (Legion/Aux)'
        STAFF     = 'STAFF',     'Staff'
        LEADER    = 'LEADER',    'Leader'
        CUSTOM    = 'CUSTOM',    'Custom'

    name = models.CharField(max_length=100, help_text="Display name, e.g. 'Knights VC'")
    slug = models.SlugField(max_length=50, unique=True, help_text="Internal key, e.g. 'knights'")
    channel_class = models.CharField(
        max_length=20,
        choices=ChannelClass.choices,
        default=ChannelClass.PUBLIC,
    )
    prefix = models.CharField(max_length=50, default='', blank=True,
                              help_text="Channel name prefix, e.g. '🔴BVK VC '")
    required_group = models.ForeignKey(
        Group,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="vc_profiles",
        help_text="Django Group required to create/switch to this profile. Leave blank = everyone.",
    )
    # Behaviour flags
    apply_voice_ban = models.BooleanField(
        default=False,
        help_text="Whether active RED voice bans are applied to channels of this type"
    )
    show_red_warning = models.BooleanField(
        default=False,
        help_text="Whether joining members receive a RED-channel DM warning"
    )
    allow_type_switch = models.BooleanField(
        default=True,
        help_text="Whether the channel owner can switch this channel to a different profile type"
    )
    allow_knights_controls = models.BooleanField(
        default=False,
        help_text="Whether to post the Knights ban/unban control panel in the VC"
    )

    # Ordering & activation
    sort_order = models.PositiveSmallIntegerField(
        default=50,
        help_text="Lower = higher priority. Evaluated highest-to-lowest when picking a profile."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()
        ordering = ['sort_order']
        verbose_name = "Voice Channel Profile"
        verbose_name_plural = "Voice Channel Profiles"

    def __str__(self):
        return f"{self.name} [{self.slug}]"


# ---------------------------------------------------------------------------
# Per-profile Discord role → VC permission mapping
# ---------------------------------------------------------------------------

class VCRolePermission(models.Model):
    """
    Defines what Discord permission overwrites are applied to a role
    when an event VC is created for this VoiceChannelProfile.

    Locked state (pre-event):  all rows applied but connect is forced False.
    Unlocked state (on start): rows applied as-is — connect True/False/None.

    None = inherit from category (no overwrite set for that flag).
    """

    profile = models.ForeignKey(
        VoiceChannelProfile,
        on_delete=models.CASCADE,
        related_name="role_permissions",
    )
    discord_role_id = models.BigIntegerField(
        help_text="Discord Role ID. Use 0 to target @everyone.",
    )
    role_label = models.CharField(
        max_length=100, blank=True,
        help_text="Human-readable label for this row (e.g. 'BVK Members'). Not used by the bot.",
    )

    # --- Permission flags ---
    # None = no overwrite (inherit). True = allow. False = deny.
    allow_view_channel  = models.BooleanField(null=True, blank=True, default=True,  help_text="Can see the channel in the sidebar.")
    allow_connect       = models.BooleanField(null=True, blank=True, default=True,  help_text="Can join the voice channel.")
    allow_speak         = models.BooleanField(null=True, blank=True, default=True,  help_text="Can speak (unmute themselves).")
    allow_stream        = models.BooleanField(null=True, blank=True, default=None,  help_text="Can stream video.")
    allow_use_soundboard= models.BooleanField(null=True, blank=True, default=None,  help_text="Can use soundboard.")
    allow_mute_members  = models.BooleanField(null=True, blank=True, default=None,  help_text="Can server-mute others.")
    allow_deafen_members= models.BooleanField(null=True, blank=True, default=None,  help_text="Can server-deafen others.")
    allow_move_members  = models.BooleanField(null=True, blank=True, default=None,  help_text="Can move members between VCs.")

    class Meta:
        default_permissions = ()
        unique_together = [("profile", "discord_role_id")]
        ordering = ["discord_role_id"]
        verbose_name = "VC Role Permission"
        verbose_name_plural = "VC Role Permissions"

    def __str__(self):
        return f"{self.profile.slug} | role {self.discord_role_id} ({self.role_label or '—'})"

    def to_discord_overwrite(self, locked: bool = False) -> dict:
        """
        Returns a kwargs dict suitable for discord.PermissionOverwrite(**kwargs).
        When locked=True, connect is forced to False regardless of allow_connect.
        """
        def flag(v):
            return v  # None → inherit, True → allow, False → deny

        kwargs = {}
        if self.allow_view_channel is not None:
            kwargs["view_channel"] = flag(self.allow_view_channel)
        if locked:
            kwargs["connect"] = False
        elif self.allow_connect is not None:
            kwargs["connect"] = flag(self.allow_connect)
        if self.allow_speak is not None:
            kwargs["speak"] = flag(self.allow_speak)
        if self.allow_stream is not None:
            kwargs["stream"] = flag(self.allow_stream)
        if self.allow_use_soundboard is not None:
            kwargs["use_soundboard"] = flag(self.allow_use_soundboard)
        if self.allow_mute_members is not None:
            kwargs["mute_members"] = flag(self.allow_mute_members)
        if self.allow_deafen_members is not None:
            kwargs["deafen_members"] = flag(self.allow_deafen_members)
        if self.allow_move_members is not None:
            kwargs["move_members"] = flag(self.allow_move_members)
        return kwargs


# ---------------------------------------------------------------------------
# Protected-channel warning acknowledgement (persists across bot restarts)
# ---------------------------------------------------------------------------

class ProtectedChannelAck(models.Model):
    """Tracks when a user last acknowledged the protected-channel warning.
    Used to send a monthly refresher instead of spamming on every join.
    """
    user_id = models.BigIntegerField(unique=True, help_text="Discord user ID.")
    last_acknowledged_at = models.DateTimeField(help_text="When the user last clicked 'I Understand'.")

    class Meta:
        default_permissions = ()
        verbose_name = "Protected Channel Acknowledgement"
        verbose_name_plural = "Protected Channel Acknowledgements"

    def __str__(self):
        return f"Ack({self.user_id}) @ {self.last_acknowledged_at:%Y-%m-%d}"


# ---------------------------------------------------------------------------
# Temporary Voice Channel
# ---------------------------------------------------------------------------

class TemporaryVoiceChannel(OriginalStateMixin, models.Model):
    """
    Tracks every bot-created temporary voice channel.
    Signals on this model drive VoiceBan permission syncing.
    """

    CHANNEL_TYPES = [
        ('PUBLIC',    'Public Channel'),
        ('PROTECTED', 'Protected Channel'),
        ('STAFF',     'Staff Channel'),
        ('LEADER',    'Leader Channel'),
        # Legacy values from orm_models era — kept for existing DB rows
        ('BLUE', 'Blue Team Channel'),
        ('RED',  'Red Team Channel'),
    ]

    channel_id       = models.BigIntegerField(primary_key=True)
    guild_id         = models.BigIntegerField(db_index=True)
    parent_channel_id = models.BigIntegerField(db_index=True)
    creator_id       = models.BigIntegerField()

    name         = models.CharField(max_length=100)
    profile_slug = models.CharField(
        max_length=50, null=True, blank=True,
        help_text="Slug of the VoiceChannelProfile that created this VC"
    )
    # Keep channel_type for backwards-compat with VoiceBan signal logic
    channel_type = models.CharField(
        max_length=10, choices=CHANNEL_TYPES, default='PUBLIC', db_index=True
    )

    user_limit        = models.IntegerField(default=0)
    bitrate           = models.IntegerField(default=64000)
    region            = models.CharField(max_length=50, default='automatic')
    control_message_id = models.BigIntegerField(null=True, blank=True)
    is_paused         = models.BooleanField(default=False)
    pause_until       = models.DateTimeField(null=True, blank=True)

    is_active    = models.BooleanField(default=True, db_index=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    objects = SignalEmittingManager()

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_temporaryvoicechannel'
        ordering = ['-created_at']
        verbose_name = "Temporary Voice Channel"
        verbose_name_plural = "Temporary Voice Channels"
        permissions = [
            ("can_manage_temp_vcs", "Can manage any temporary voice channel (override creator requirement)"),
        ]
        indexes = [
            models.Index(fields=['guild_id', 'is_active']),
            models.Index(fields=['creator_id']),
            models.Index(fields=['parent_channel_id']),
            models.Index(fields=['channel_type', 'is_active']),
        ]

    def __str__(self):
        status = 'Active' if self.is_active else 'Inactive'
        return f"{self.name} ({self.get_channel_type_display()}) — {status}"

    @property
    def is_red_channel(self):
        return self.channel_type == 'RED'

    @property
    def pause_remaining(self):
        if not self.is_paused or not self.pause_until:
            return 0
        remaining = self.pause_until - timezone.now()
        if remaining.total_seconds() <= 0:
            return 0
        return int(remaining.total_seconds() / 60)


# ---------------------------------------------------------------------------
# Voice Ban
# ---------------------------------------------------------------------------

class VoiceBanManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().annotate(
            is_expired=models.Case(
                models.When(expires_at__lte=models.functions.Now(), then=True),
                default=False,
                output_field=models.BooleanField()
            )
        )


def _history_user_getter(historical_instance):
    return historical_instance.history_user_id


def _history_user_setter(historical_instance, user):
    historical_instance.history_user_id = user


class VoiceBan(models.Model):
    class Kind(models.TextChoices):
        RED = 'REDC', 'All Red / Protected Channels'
        ONE = 'ONEC', 'A Specific Channel'

    user_id              = models.BigIntegerField(db_index=True)
    invoked_at           = models.DateTimeField(auto_now_add=True)
    expires_at           = models.DateTimeField(blank=True, null=True)
    reason               = models.TextField(blank=True, null=True)
    comment              = models.TextField(blank=True, null=True)
    kind                 = models.CharField(max_length=4, choices=Kind.choices, default=Kind.RED, db_index=True)
    kind_specific_metadata = models.JSONField(default=dict)

    objects = VoiceBanManager()

    history = HistoricalRecords(
        history_user_id_field=models.BigIntegerField(null=True),
        history_user_getter=_history_user_getter,
        history_user_setter=_history_user_setter
    )

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_voiceban'
        permissions = [
            ("can_manage_voice_bans", "Can manage voice bans via bot commands"),
        ]

    def __getattr__(self, name):
        if name == 'is_expired':
            if self.expires_at:
                return timezone.now() >= self.expires_at
            return False
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __str__(self):
        expires_str = f"until {self.expires_at}" if self.expires_at else "permanently"
        return f"User {self.user_id} banned from {self.get_kind_display()} {expires_str}"


# ---------------------------------------------------------------------------
# Voice Session
# ---------------------------------------------------------------------------

class VoiceSession(models.Model):
    """Tracks individual voice channel sessions with quality metrics."""

    SESSION_QUALITY_CHOICES = [
        ('EXCELLENT', 'Excellent (>80% talking)'),
        ('GOOD',      'Good (50-80% talking)'),
        ('FAIR',      'Fair (20-50% talking)'),
        ('POOR',      'Poor (<20% talking)'),
        ('INACTIVE',  'Inactive (muted/deafened/afk)'),
    ]

    user_id      = models.BigIntegerField(db_index=True)
    guild_id     = models.BigIntegerField(db_index=True)
    channel_id   = models.BigIntegerField(db_index=True)
    channel_name = models.CharField(max_length=200, null=True, blank=True)
    channel_type = models.CharField(max_length=50,  null=True, blank=True)

    join_time  = models.DateTimeField()
    leave_time = models.DateTimeField(null=True, blank=True)

    was_muted     = models.BooleanField(default=False)
    was_deafened  = models.BooleanField(default=False)
    was_streaming = models.BooleanField(default=False)
    was_video     = models.BooleanField(default=False)
    was_afk       = models.BooleanField(default=False)

    duration_seconds     = models.IntegerField(null=True, blank=True)
    talking_time_seconds = models.IntegerField(default=0)
    talking_percentage   = models.FloatField(default=0.0)
    session_quality      = models.CharField(max_length=20, choices=SESSION_QUALITY_CHOICES, default='FAIR')

    ended_normally      = models.BooleanField(default=True)
    disconnect_reason   = models.CharField(max_length=100, null=True, blank=True)
    device_info         = models.JSONField(default=dict, null=True, blank=True)
    session_restored    = models.BooleanField(default=False)
    checkpoint_id       = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_voicesession'
        ordering = ['-join_time']
        indexes = [
            models.Index(fields=['user_id', 'join_time']),
            models.Index(fields=['guild_id', 'channel_id']),
            models.Index(fields=['join_time', 'leave_time']),
            models.Index(fields=['checkpoint_id']),
        ]
        constraints = [
            models.CheckConstraint(name='positive_duration',       condition=models.Q(duration_seconds__gte=0)),
            models.CheckConstraint(name='positive_talking_time',   condition=models.Q(talking_time_seconds__gte=0)),
            models.CheckConstraint(name='valid_talking_percentage', condition=models.Q(talking_percentage__gte=0) & models.Q(talking_percentage__lte=100)),
        ]

    def __str__(self):
        return f"VoiceSession: {self.user_id} in {self.channel_name} ({self.duration_seconds}s)"

    def calculate_metrics(self):
        if self.leave_time and self.join_time:
            self.duration_seconds = int((self.leave_time - self.join_time).total_seconds())
            if self.was_muted or self.was_deafened or self.was_afk:
                self.talking_time_seconds = 0
                self.session_quality = 'INACTIVE'
            else:
                self.talking_time_seconds = self.duration_seconds

            if self.duration_seconds > 0:
                self.talking_percentage = (self.talking_time_seconds / self.duration_seconds) * 100
            else:
                self.talking_percentage = 0.0

            if self.session_quality != 'INACTIVE':
                if self.talking_percentage >= 80:
                    self.session_quality = 'EXCELLENT'
                elif self.talking_percentage >= 50:
                    self.session_quality = 'GOOD'
                elif self.talking_percentage >= 20:
                    self.session_quality = 'FAIR'
                else:
                    self.session_quality = 'POOR'

    def save(self, *args, **kwargs):
        if self.join_time and timezone.is_naive(self.join_time):
            self.join_time = timezone.make_aware(self.join_time)
        if self.leave_time and timezone.is_naive(self.leave_time):
            self.leave_time = timezone.make_aware(self.leave_time)
        if self.leave_time:
            self.calculate_metrics()
        super().save(*args, **kwargs)

    @property
    def is_complete(self):
        return self.leave_time is not None

    @property
    def duration_formatted(self):
        if not self.duration_seconds:
            return "0s"
        h = self.duration_seconds // 3600
        m = (self.duration_seconds % 3600) // 60
        s = self.duration_seconds % 60
        if h > 0:
            return f"{h}h {m}m {s}s"
        elif m > 0:
            return f"{m}m {s}s"
        return f"{s}s"


# ---------------------------------------------------------------------------
# Voice Session Checkpoint (crash recovery)
# ---------------------------------------------------------------------------

class VoiceSessionCheckpoint(models.Model):
    """Persists in-progress voice sessions so the bot can recover after restart."""

    checkpoint_id = models.CharField(max_length=100, unique=True, db_index=True)
    user_id       = models.BigIntegerField(db_index=True)
    guild_id      = models.BigIntegerField()
    channel_id    = models.BigIntegerField()
    channel_name  = models.CharField(max_length=200)

    join_time    = models.DateTimeField()
    last_update  = models.DateTimeField(auto_now=True)

    is_muted      = models.BooleanField(default=False)
    is_deafened   = models.BooleanField(default=False)
    is_afk        = models.BooleanField(default=False)
    is_streaming  = models.BooleanField(default=False)

    talking_start_time          = models.DateTimeField(null=True, blank=True)
    accumulated_talking_seconds = models.IntegerField(default=0)

    device_info = models.JSONField(default=dict)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_voicesessioncheckpoint'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['checkpoint_id', 'is_active']),
            models.Index(fields=['user_id', 'is_active']),
        ]

    def __str__(self):
        return f"Checkpoint: {self.checkpoint_id} for {self.user_id}"


# ---------------------------------------------------------------------------
# Voice Activity Summary (aggregated stats)
# ---------------------------------------------------------------------------

class VoiceActivitySummary(models.Model):
    PERIOD_CHOICES = [
        ('DAILY',   'Daily'),
        ('WEEKLY',  'Weekly'),
        ('MONTHLY', 'Monthly'),
        ('TOTAL',   'All Time'),
    ]

    user_id     = models.BigIntegerField(db_index=True)
    guild_id    = models.BigIntegerField(db_index=True)
    period_type = models.CharField(max_length=10, choices=PERIOD_CHOICES, default='DAILY')
    period_start = models.DateTimeField()
    period_end   = models.DateTimeField()

    total_sessions         = models.IntegerField(default=0)
    total_duration_seconds = models.IntegerField(default=0)
    total_talking_seconds  = models.IntegerField(default=0)
    unique_channels        = models.IntegerField(default=0)

    excellent_sessions = models.IntegerField(default=0)
    good_sessions      = models.IntegerField(default=0)
    fair_sessions      = models.IntegerField(default=0)
    poor_sessions      = models.IntegerField(default=0)
    inactive_sessions  = models.IntegerField(default=0)

    avg_session_duration   = models.IntegerField(default=0)
    avg_talking_percentage = models.FloatField(default=0.0)

    talking_efficiency = models.FloatField(default=0.0)
    consistency_score  = models.FloatField(default=0.0)
    streak_days        = models.IntegerField(default=0)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_voiceactivitysummary'
        unique_together = ['user_id', 'guild_id', 'period_type', 'period_start']
        indexes = [
            models.Index(fields=['user_id', 'period_type', 'period_start']),
            models.Index(fields=['guild_id', 'period_type', 'period_end']),
        ]

    def __str__(self):
        return f"VoiceSummary: {self.user_id} {self.period_type} ({self.total_duration_seconds}s)"

    def save(self, *args, **kwargs):
        if self.total_duration_seconds > 0:
            self.talking_efficiency = (self.total_talking_seconds / self.total_duration_seconds) * 100
        super().save(*args, **kwargs)

    @property
    def total_duration_formatted(self):
        if not self.total_duration_seconds:
            return "0s"
        h = self.total_duration_seconds // 3600
        m = (self.total_duration_seconds % 3600) // 60
        return f"{h}h {m}m" if h > 0 else f"{m}m"


# ---------------------------------------------------------------------------
# Event Attendance
# ---------------------------------------------------------------------------

class EventAttendance(models.Model):
    class EventType(models.TextChoices):
        ORGANIC   = 'ORGANIC',   'Organic Event'
        SCHEDULED = 'SCHEDULED', 'Scheduled Event'
        TRAINING  = 'TRAINING',  'Training Session'
        MEETING   = 'MEETING',   'Organisation Meeting'
        SOCIAL    = 'SOCIAL',    'Social Gathering'
        OPERATION = 'OPERATION', 'Military Operation'

    event_id   = models.CharField(max_length=100, db_index=True)
    event_name = models.CharField(max_length=200)
    event_type = models.CharField(max_length=20, choices=EventType.choices)

    organizer_id     = models.BigIntegerField()
    voice_channel_id = models.BigIntegerField(null=True, blank=True)
    thread_id        = models.BigIntegerField(null=True, blank=True)

    actual_start    = models.DateTimeField()
    actual_end      = models.DateTimeField(null=True, blank=True)
    scheduled_start = models.DateTimeField(null=True, blank=True)

    total_participants         = models.IntegerField(default=0)
    average_attendance_duration = models.IntegerField(default=0)
    max_concurrent_participants = models.IntegerField(default=0)

    tags        = models.JSONField(default=list)
    description = models.TextField(null=True, blank=True)

    # Crash recovery checkpoint
    event_checkpoint = models.JSONField(default=dict)
    is_recovered     = models.BooleanField(default=False)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_eventattendance'
        ordering = ['-actual_start']
        indexes = [
            models.Index(fields=['event_id', 'event_type']),
            models.Index(fields=['organizer_id', 'actual_start']),
            models.Index(fields=['voice_channel_id']),
        ]

    def __str__(self):
        return f"Event: {self.event_name} ({self.event_type})"

    @property
    def is_active(self):
        return self.actual_end is None

    @property
    def duration_seconds(self):
        end = self.actual_end or timezone.now()
        return int((end - self.actual_start).total_seconds())


class EventAttendanceRecord(models.Model):
    class AttendanceStatus(models.TextChoices):
        ACTIVE      = 'ACTIVE',      'Currently Active'
        PRESENT     = 'PRESENT',     'Present'
        LATE        = 'LATE',        'Arrived Late'
        LEFT_EARLY  = 'LEFT_EARLY',  'Left Early'
        EXCUSED     = 'EXCUSED',     'Excused Absence'
        ABSENT      = 'ABSENT',      'Absent'

    event    = models.ForeignKey(EventAttendance, on_delete=models.CASCADE, related_name='attendees')
    user_id  = models.BigIntegerField(db_index=True)
    status   = models.CharField(max_length=20, choices=AttendanceStatus.choices, default='ACTIVE')

    join_time  = models.DateTimeField()
    leave_time = models.DateTimeField(null=True, blank=True)

    voice_session         = models.ForeignKey(VoiceSession, on_delete=models.SET_NULL, null=True, blank=True)
    total_talking_seconds = models.IntegerField(default=0)
    was_active_participant = models.BooleanField(default=False)
    participation_score    = models.FloatField(default=0.0)

    was_muted    = models.BooleanField(default=False)
    was_deafened = models.BooleanField(default=False)
    was_afk      = models.BooleanField(default=False)

    verified_by        = models.BigIntegerField(null=True, blank=True)
    verification_notes = models.TextField(null=True, blank=True)

    checkpoint_id = models.CharField(max_length=100, null=True, blank=True)
    is_restored   = models.BooleanField(default=False)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_eventattendancerecord'
        unique_together = ['event', 'user_id']
        indexes = [
            models.Index(fields=['event', 'user_id']),
            models.Index(fields=['user_id', 'join_time']),
            models.Index(fields=['checkpoint_id']),
        ]
        constraints = [
            models.CheckConstraint(name='attendance_positive_talking', condition=models.Q(total_talking_seconds__gte=0)),
            models.CheckConstraint(name='valid_participation_score',   condition=models.Q(participation_score__gte=0) & models.Q(participation_score__lte=100)),
        ]

    def __str__(self):
        return f"Attendance: {self.user_id} at {self.event.event_name}"

    @property
    def attendance_duration(self):
        end = self.leave_time or timezone.now()
        return int((end - self.join_time).total_seconds())

    @property
    def talking_percentage(self):
        d = self.attendance_duration
        return (self.total_talking_seconds / d * 100) if d > 0 else 0.0

    def calculate_participation_score(self):
        duration = self.attendance_duration
        if duration == 0:
            self.participation_score = 0.0
            return
        talk_score = min((self.total_talking_seconds / duration) * 100, 100) * 0.7
        duration_score = min(duration / 3600, 1.0) * 30
        self.participation_score = round(talk_score + duration_score, 2)
        self.was_active_participant = self.participation_score >= 40

    def save(self, *args, **kwargs):
        self.calculate_participation_score()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Nomination System
# ---------------------------------------------------------------------------

class Nomination(models.Model):
    class Status(models.TextChoices):
        PENDING    = 'PENDING',    'Pending Approval'
        APPROVED   = 'APPROVED',   'Approved - Nomination Awarded'
        RECOGNIZED = 'RECOGNIZED', 'Approved - Recognition Granted'
        DENIED     = 'DENIED',     'Denied'

    class EventSource(models.TextChoices):
        MANUAL    = 'MANUAL',    'Manually entered'
        ORGANIC   = 'ORGANIC',   'Organic Event System'
        SCHEDULED = 'SCHEDULED', 'Scheduled Event'

    nominee_id          = models.BigIntegerField(db_index=True)
    nominator_id        = models.BigIntegerField(db_index=True)
    nominee_username    = models.CharField(max_length=100)
    nominator_username  = models.CharField(max_length=100)

    event_date   = models.CharField(max_length=200)
    event_id     = models.CharField(max_length=100, null=True, blank=True)
    event_source = models.CharField(max_length=20, choices=EventSource.choices, default=EventSource.MANUAL)

    reason       = models.TextField()
    status       = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    processed_by_id       = models.BigIntegerField(null=True, blank=True)
    processed_by_username = models.CharField(max_length=100, null=True, blank=True)
    denial_reason         = models.TextField(null=True, blank=True)

    created_at    = models.DateTimeField(auto_now_add=True)
    processed_at  = models.DateTimeField(null=True, blank=True)
    flags         = models.JSONField(default=dict)

    approval_message_id = models.BigIntegerField(null=True, blank=True)
    approval_channel_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_nomination'
        ordering = ['-created_at']
        verbose_name = "Nomination"
        verbose_name_plural = "Nominations"
        permissions = [
            ('can_approve_nominations', 'Can approve or deny nominations'),
            ('can_submit_nominations',  'Can submit nominations for members'),
            ('can_self_nominate',       'Can submit a self-nomination for distinction'),
        ]
        indexes = [
            models.Index(fields=['nominee_id', 'status']),
            models.Index(fields=['nominator_id']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['event_id']),
        ]

    def __str__(self):
        return f"Nomination: {self.nominee_username} by {self.nominator_username} ({self.status})"


class DistinctionLevel(models.Model):
    level                 = models.IntegerField(unique=True)
    nominations_required  = models.IntegerField()
    role_id               = models.BigIntegerField()
    role_name             = models.CharField(max_length=100)
    description           = models.TextField(null=True, blank=True)
    color                 = models.CharField(max_length=7, default='#0099FF')
    perks                 = models.JSONField(default=list)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_distinctionlevel'
        ordering = ['level']
        verbose_name = "Distinction Level"
        verbose_name_plural = "Distinction Levels"
        constraints = [
            models.CheckConstraint(
                name='distinction_level_range',
                condition=models.Q(level__gte=1) & models.Q(level__lte=10),
            ),
        ]

    def __str__(self):
        return f"Distinction Level {self.level}: {self.role_name} ({self.nominations_required} nominations)"


class UserDistinction(models.Model):
    user_id             = models.BigIntegerField(unique=True, db_index=True)
    current_level       = models.IntegerField(default=0)
    total_nominations   = models.IntegerField(default=0)
    total_recognitions  = models.IntegerField(default=0)
    current_streak      = models.IntegerField(default=0)
    longest_streak      = models.IntegerField(default=0)
    last_nomination_date = models.DateTimeField(null=True, blank=True)
    nomination_rate     = models.FloatField(default=0.0)
    approval_rate       = models.FloatField(default=0.0)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)
    last_level_up       = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_userdistinction'
        verbose_name = "User Distinction"
        verbose_name_plural = "User Distinctions"
        indexes = [
            models.Index(fields=['user_id', 'current_level']),
            models.Index(fields=['current_level']),
            models.Index(fields=['total_nominations']),
        ]

    def __str__(self):
        return f"User {self.user_id}: Level {self.current_level}, {self.total_nominations} nominations"

    @property
    def nominations_until_next_level(self) -> int:
        from asgiref.sync import async_to_sync
        try:
            next_level = DistinctionLevel.objects.filter(
                level__gt=self.current_level
            ).order_by('level').first()
            if next_level:
                return max(0, next_level.nominations_required - self.total_nominations)
        except Exception:
            pass
        return 0


class EventReference(models.Model):
    class SourceSystem(models.TextChoices):
        ORGANIC    = 'ORGANIC',    'Organic Event System'
        SCHEDULED  = 'SCHEDULED',  'Scheduled Event System'
        MANUAL     = 'MANUAL',     'Manual Entry'
        VC_TRACKING = 'VC_TRACKING', 'Voice Channel Tracking'
        ATTENDANCE = 'ATTENDANCE', 'Attendance System'

    event_id            = models.CharField(max_length=100, db_index=True)
    source_system       = models.CharField(max_length=20, choices=SourceSystem.choices)
    event_name          = models.CharField(max_length=200)
    event_description   = models.TextField(null=True, blank=True)
    event_date          = models.DateTimeField()
    organizer_id        = models.BigIntegerField(null=True, blank=True)
    organizer_username  = models.CharField(max_length=100, null=True, blank=True)
    participant_ids     = models.JSONField(default=list)
    metadata            = models.JSONField(default=dict)
    is_archived         = models.BooleanField(default=False)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_eventreference'
        unique_together = [['event_id', 'source_system']]
        verbose_name = "Event Reference"
        verbose_name_plural = "Event References"
        indexes = [
            models.Index(fields=['event_id', 'source_system']),
            models.Index(fields=['event_date']),
            models.Index(fields=['organizer_id']),
        ]

    def __str__(self):
        return f"{self.event_name} ({self.source_system})"


# ---------------------------------------------------------------------------
# Welcome System — member lifecycle tracking
# ---------------------------------------------------------------------------

class UserJoinRecord(models.Model):
    """Comprehensive tracking system for Discord member lifecycle events."""

    discord_id          = models.BigIntegerField(db_index=True, verbose_name="Discord ID")
    username            = models.CharField(max_length=100, verbose_name="Username")
    global_name         = models.CharField(max_length=100, null=True, blank=True, verbose_name="Global Name")
    discriminator       = models.CharField(max_length=4, null=True, blank=True, verbose_name="Discriminator")
    display_name        = models.CharField(max_length=100, null=True, blank=True, verbose_name="Server Display Name")
    avatar_url          = models.URLField(max_length=500, null=True, blank=True, verbose_name="Avatar URL")
    avatar_hash         = models.CharField(max_length=100, null=True, blank=True, verbose_name="Avatar Hash")

    is_active           = models.BooleanField(default=True, db_index=True, verbose_name="Active Member")
    membership_status   = models.CharField(
        max_length=20,
        choices=[
            ('ACTIVE',    'Active Member'),
            ('LEFT',      'Voluntarily Left'),
            ('KICKED',    'Kicked from Server'),
            ('BANNED',    'Banned from Server'),
            ('INACTIVE',  'Inactive (Auto-removed)'),
        ],
        default='ACTIVE',
        verbose_name="Membership Status",
    )

    first_seen              = models.DateTimeField(null=True, blank=True, verbose_name="First Seen")
    joined_at               = models.DateTimeField(auto_now_add=True, verbose_name="Join Timestamp")
    left_at                 = models.DateTimeField(null=True, blank=True, verbose_name="Left At")
    last_seen               = models.DateTimeField(auto_now=True, verbose_name="Last Seen")
    last_updated            = models.DateTimeField(auto_now=True, verbose_name="Last Updated")

    join_count              = models.PositiveIntegerField(default=1, verbose_name="Join Count")
    total_duration          = models.DurationField(null=True, blank=True, verbose_name="Total Duration")
    current_streak_start    = models.DateTimeField(null=True, blank=True, verbose_name="Current Streak Start")
    longest_streak          = models.DurationField(null=True, blank=True, verbose_name="Longest Streak")

    message_count           = models.PositiveIntegerField(default=0, verbose_name="Message Count")
    last_message_at         = models.DateTimeField(null=True, blank=True, verbose_name="Last Message")

    welcome_message_id      = models.BigIntegerField(null=True, blank=True, verbose_name="Welcome Message ID")
    join_log_message_id     = models.BigIntegerField(null=True, blank=True, verbose_name="Join Log Message ID")
    welcome_image_generated = models.BooleanField(default=False, verbose_name="Welcome Image Generated")
    welcome_image_path      = models.CharField(max_length=500, null=True, blank=True, verbose_name="Welcome Image Path")

    flags   = models.JSONField(default=dict, verbose_name="User Flags")
    notes   = models.TextField(null=True, blank=True, verbose_name="Admin Notes")

    class Meta:
        default_permissions = ()
        db_table = 'orm_models_userjoinrecord'
        ordering = ['-joined_at']
        verbose_name = "User Join Record"
        verbose_name_plural = "User Join Records"
        indexes = [
            models.Index(fields=['discord_id', 'is_active']),
            models.Index(fields=['discord_id', 'membership_status']),
            models.Index(fields=['joined_at']),
            models.Index(fields=['left_at']),
            models.Index(fields=['last_seen']),
            models.Index(fields=['username']),
            models.Index(fields=['global_name']),
            models.Index(fields=['join_count']),
        ]

    def __str__(self):
        if self.global_name:
            return f"{self.global_name} (@{self.username}) - {self.membership_status}"
        elif self.discriminator and self.discriminator != '0':
            return f"{self.username}#{self.discriminator} - {self.membership_status}"
        return f"@{self.username} - {self.membership_status}"

    def save(self, *args, **kwargs):
        if not self.first_seen:
            self.first_seen = self.joined_at
        if self.is_active and not self.current_streak_start:
            self.current_streak_start = self.joined_at
        if not self.is_active and self.current_streak_start:
            streak_duration = self.left_at - self.current_streak_start
            if not self.longest_streak or streak_duration > self.longest_streak:
                self.longest_streak = streak_duration
            self.current_streak_start = None
        super().save(*args, **kwargs)

    @property
    def discord_tag(self):
        if self.global_name:
            return self.global_name
        elif self.discriminator and self.discriminator != '0':
            return f"{self.username}#{self.discriminator}"
        return self.username

    @property
    def current_streak_duration(self):
        if self.is_active and self.current_streak_start:
            return timezone.now() - self.current_streak_start
        return None

    @property
    def is_returning_member(self):
        return self.join_count > 1

    @property
    def avatar_display_url(self):
        if self.avatar_url:
            return self.avatar_url
        default_num = int(self.discriminator) % 5 if self.discriminator else 0
        return f"https://cdn.discordapp.com/embed/avatars/{default_num}.png"

    @property
    def membership_duration(self):
        if self.is_active and self.current_streak_start:
            return timezone.now() - self.current_streak_start
        elif self.left_at and self.current_streak_start:
            return self.left_at - self.current_streak_start
        return None

    def record_message(self, timestamp=None):
        self.message_count += 1
        self.last_message_at = timestamp or self.last_seen
        self.save(update_fields=['message_count', 'last_message_at', 'last_seen'])

    def mark_as_left(self, status='LEFT', timestamp=None):
        self.is_active = False
        self.membership_status = status
        self.left_at = timestamp or self.last_seen
        self.save(update_fields=['is_active', 'membership_status', 'left_at', 'last_updated'])

    def mark_as_returned(self, timestamp=None):
        self.is_active = True
        self.membership_status = 'ACTIVE'
        self.join_count += 1
        self.current_streak_start = timestamp or self.last_seen
        self.left_at = None
        self.save(update_fields=['is_active', 'membership_status', 'join_count',
                                 'current_streak_start', 'left_at', 'last_updated'])


# ---------------------------------------------------------------------------
# VC Transcript — message history captured before a temp VC is deleted
# ---------------------------------------------------------------------------

class VCTranscript(models.Model):
    channel_id   = models.BigIntegerField(db_index=True)
    channel_name = models.CharField(max_length=100)
    guild_id     = models.BigIntegerField()
    creator_id   = models.BigIntegerField()
    profile_slug = models.CharField(max_length=50, null=True, blank=True)
    channel_type = models.CharField(max_length=10)
    vc_created_at = models.DateTimeField()
    deleted_at   = models.DateTimeField(auto_now_add=True)
    message_count = models.IntegerField(default=0)

    class Meta:
        default_permissions = ()
        ordering = ['-deleted_at']
        verbose_name = "VC Transcript"
        verbose_name_plural = "VC Transcripts"
        permissions = [
            ('view_vctranscript', 'Can view VC transcripts'),
        ]
        indexes = [
            models.Index(fields=['guild_id', 'deleted_at']),
            models.Index(fields=['creator_id']),
        ]

    def __str__(self):
        return f"Transcript: {self.channel_name} (deleted {self.deleted_at:%Y-%m-%d %H:%M})"


class VCTranscriptMessage(models.Model):
    transcript      = models.ForeignKey(VCTranscript, on_delete=models.CASCADE, related_name='messages')
    message_id      = models.BigIntegerField()
    author_id       = models.BigIntegerField()
    author_name     = models.CharField(max_length=200)
    author_avatar_url = models.URLField(max_length=500, null=True, blank=True)
    is_bot          = models.BooleanField(default=False)
    content         = models.TextField(blank=True)
    attachments     = models.JSONField(default=list)
    embeds_count    = models.IntegerField(default=0)
    created_at      = models.DateTimeField()

    class Meta:
        default_permissions = ()
        ordering = ['created_at']
        verbose_name = "VC Transcript Message"
        verbose_name_plural = "VC Transcript Messages"

    def __str__(self):
        return f"Msg {self.message_id} by {self.author_name}"
