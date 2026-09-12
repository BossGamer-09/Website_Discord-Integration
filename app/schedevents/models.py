"""
app/schedevents/models.py

Sesh.fyi-style event scheduling system with:
  - Discord native scheduled event sync (bi-directional)
  - RSVP (Going/Maybe/Not Going) with capacity + waitlist
  - Recurring events via RFC 5545 RRULE
  - Auto VC spawning via VoiceChannelProfile
  - Post-event attendance reports linked to disfunction.EventAttendance
"""
from datetime import timedelta

import coolname
from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from simple_history.models import HistoricalRecords

from app.main.util.db import OriginalStateMixin, SignalEmittingManager


# ---------------------------------------------------------------------------
# EventIndicator — security/access classification for events
# ---------------------------------------------------------------------------

class EventIndicator(models.Model):
    """
    Security/access level label shown on event embeds.
    Examples: PUBLIC ◻️, BLUE 🔵 (members only), RED 🔴 (staff), VEILED 🟣 (secret), COMP 🔶
    """
    id = models.CharField(
        max_length=25, primary_key=True,
        help_text="Short uppercase code, e.g. 'PUBLIC', 'BLUE'"
    )
    prefix = models.CharField(max_length=16, default="◻️", blank=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    color_hex = models.CharField(
        max_length=7, default="#5865F2",
        help_text="Hex color used for the Discord embed border"
    )

    class Meta:
        default_permissions = ()
        ordering = ["id"]
        permissions = [
            ("can_create_events_with", "Can create events using this indicator"),
        ]

    def __str__(self):
        return f"{self.prefix} {self.name}"

    @property
    def discord_color(self) -> int:
        """Return the color as an integer for discord.py embeds."""
        return int(self.color_hex.lstrip("#"), 16)


# ---------------------------------------------------------------------------
# EventPlan — the core scheduled event record
# ---------------------------------------------------------------------------

class EventPlan(OriginalStateMixin, models.Model):
    """
    A scheduled org event.  Lifecycle: DRAFT → PUBLISHED → ACTIVE → COMPLETED/CANCELLED.

    On PUBLISHED: a Discord GuildScheduledEvent is created (via IPC → EventLifecycleCog).
    On ACTIVE:    a VC is spawned and disfunction attendance tracking is started.
    On COMPLETED: EventReport is generated, no-shows are tallied.
    """

    class Status(models.TextChoices):
        DRAFT     = "DRAFT",     "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        ACTIVE    = "ACTIVE",    "Active"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    # --- Identity ---
    codename_id = models.CharField(max_length=255, primary_key=True, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(max_length=2000, blank=True, default="")
    indicator = models.ForeignKey(
        EventIndicator, on_delete=models.PROTECT, related_name="events"
    )

    # --- People ---
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_schedevents",
    )
    organizers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="organized_schedevents",
        blank=True,
    )

    # --- Timing ---
    planned_start_at = models.DateTimeField()
    planned_duration = models.DurationField(
        default=timedelta(hours=2),
        help_text="How long the event runs",
    )
    timezone_name = models.CharField(
        max_length=64, default="UTC",
        help_text="IANA timezone name displayed to members, e.g. 'America/New_York'"
    )

    # --- Recurring ---
    # Store as RFC 5545 RRULE body (without the 'RRULE:' prefix).
    # Examples: "FREQ=WEEKLY;BYDAY=MO"  /  "FREQ=DAILY;COUNT=5"
    recurring_rule = models.CharField(
        max_length=512, blank=True, null=True,
        help_text="RFC 5545 RRULE string (omit 'RRULE:' prefix)"
    )
    recurring_until = models.DateTimeField(null=True, blank=True)
    parent_event = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="recurring_instances",
        help_text="Parent template event when this is a recurring child"
    )

    # --- Status ---
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    # --- Location ---
    location = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Where the event takes place (shown in Discord scheduled event)."
    )

    # --- Discord integration ---
    guild_id = models.BigIntegerField(db_index=True)
    discord_scheduled_event_id = models.BigIntegerField(null=True, blank=True, unique=True)
    announcement_channel_override_id = models.BigIntegerField(
        null=True, blank=True,
        help_text="Override the global announcement channel for this event. Leave blank to use the default.",
    )
    announcement_channel_id = models.BigIntegerField(null=True, blank=True)
    announcement_message_id = models.BigIntegerField(null=True, blank=True)
    attendee_thread_id = models.BigIntegerField(null=True, blank=True)
    lookback_thread_id = models.BigIntegerField(null=True, blank=True)

    # --- VC integration ---
    auto_generate_vc = models.BooleanField(default=True)
    vc_profile = models.ForeignKey(
        "disfunction.VoiceChannelProfile",
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name="scheduled_events",
        help_text="Which VC profile to use when spawning the event VC. Null = default preference.",
    )
    active_vc_channel_id = models.BigIntegerField(null=True, blank=True)

    # --- Thread settings ---
    private_thread = models.BooleanField(
        default=False,
        help_text="Create the attendee thread as a private thread (requires server boost level 2). "
                  "Only RSVP'd members are added; non-RSVP'd members cannot see it.",
    )

    # --- RSVP config ---
    capacity = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Max GOING RSVPs. Null = unlimited."
    )
    waitlist_enabled = models.BooleanField(default=True)
    rsvp_deadline = models.DateTimeField(null=True, blank=True)

    # --- Display ---
    cover_image_url = models.URLField(max_length=512, blank=True, null=True)
    color_hex = models.CharField(
        max_length=7, blank=True, null=True,
        help_text="Per-event embed color override (#RRGGBB). Overrides the indicator color."
    )
    hide_attendees = models.BooleanField(default=False)

    # --- Sesh-compatible extra config ---
    mentions_on_create = models.JSONField(
        default=list, blank=True,
        help_text="Discord role IDs (ints) to ping when event is published.",
    )
    mentions_on_start = models.JSONField(
        default=list, blank=True,
        help_text="Discord role IDs (ints) to ping when event goes ACTIVE.",
    )
    mentions_on_vc_unlock = models.JSONField(
        default=list, blank=True,
        help_text="Discord role IDs (ints) to ping when the event VC unlocks (~30m before start).",
    )
    allow_maintain_rsvp = models.BooleanField(
        default=False,
        help_text="Carry RSVPs forward when a recurring event repeats.",
    )

    # --- Thread settings (expanded) ---
    thread_title_template = models.CharField(
        max_length=200, default="$EventName", blank=True,
        help_text="Thread name template. Supports $EventName.",
    )

    class ThreadArchiveDuration(models.TextChoices):
        ONE_HOUR   = "OneHour",   "1 Hour"
        ONE_DAY    = "OneDay",    "1 Day"
        THREE_DAYS = "ThreeDays", "3 Days"
        ONE_WEEK   = "OneWeek",   "1 Week"

    thread_auto_archive_duration = models.CharField(
        max_length=20,
        choices=ThreadArchiveDuration.choices,
        default=ThreadArchiveDuration.ONE_DAY,
    )
    thread_join_on_rsvp = models.BooleanField(
        default=True,
        help_text="Auto-add members to attendee thread when they RSVP GOING.",
    )

    class ThreadStartMessageType(models.TextChoices):
        THREAD  = "Thread",  "Post in thread"
        CHANNEL = "Channel", "Post in channel"
        NONE    = "None",    "No start message"

    thread_start_message_type = models.CharField(
        max_length=10,
        choices=ThreadStartMessageType.choices,
        default=ThreadStartMessageType.THREAD,
    )

    # --- RSVP button emojis ---
    # Unicode (1 char) OR Discord custom emoji like "<:name:1234567890>" or "<a:name:1234567890>"
    emoji_going     = models.CharField(max_length=64, default="✅", blank=True)
    emoji_maybe     = models.CharField(max_length=64, default="❓", blank=True)
    emoji_not_going = models.CharField(max_length=64, default="❌", blank=True)

    # --- Audit ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()
    objects = SignalEmittingManager()

    class Meta:
        default_permissions = ()
        ordering = ["planned_start_at"]
        permissions = [
            ("can_modify_delete_created",   "Can modify/delete events they created"),
            ("can_modify_delete_organizer",  "Can modify/delete events they organize"),
            ("can_publish_events",           "Can publish events"),
            ("can_force_checkin",            "Can manually check in attendees"),
            ("can_manage_all_events",        "Can manage any event regardless of ownership"),
        ]

    # --- Codename generation ---

    def save(self, *args, **kwargs):
        if not self.codename_id:
            max_retries = 7
            for attempt in range(max_retries):
                words = coolname.generate(2 + attempt)
                self.codename_id = "".join(w.capitalize() for w in words)
                try:
                    with transaction.atomic():
                        kwargs["force_insert"] = True
                        return super().save(*args, **kwargs)
                except IntegrityError:
                    if attempt == max_retries - 1:
                        raise
        else:
            kwargs.pop("force_insert", None)
            return super().save(*args, **kwargs)

    @property
    def planned_end_at(self):
        return self.planned_start_at + self.planned_duration

    @property
    def going_count(self):
        return self.rsvps.filter(status=EventRSVP.RSVPStatus.GOING).count()

    @property
    def maybe_count(self):
        return self.rsvps.filter(status=EventRSVP.RSVPStatus.MAYBE).count()

    @property
    def waitlist_count(self):
        return self.rsvps.filter(status=EventRSVP.RSVPStatus.WAITLISTED).count()

    @property
    def is_full(self):
        return self.capacity is not None and self.going_count >= self.capacity

    def __str__(self):
        return f"[{self.codename_id}] {self.title}"


# ---------------------------------------------------------------------------
# EventRSVP — per-user signup record
# ---------------------------------------------------------------------------

class EventRSVP(OriginalStateMixin, models.Model):
    """
    Tracks one user's RSVP status for one EventPlan.
    Also stores check-in info and the post-event attendance outcome.
    """

    class RSVPStatus(models.TextChoices):
        GOING      = "GOING",      "Going"
        MAYBE      = "MAYBE",      "Maybe"
        NOT_GOING  = "NOT_GOING",  "Not Going"
        WAITLISTED = "WAITLISTED", "Waitlisted"
        PENDING    = "PENDING",    "Pending Approval"

    class AttendanceOutcome(models.TextChoices):
        PRESENT    = "PRESENT",    "Present"
        LATE       = "LATE",       "Late (joined after grace period)"
        LEFT_EARLY = "LEFT_EARLY", "Left Early"
        ABSENT     = "ABSENT",     "No-show"

    class CheckInSource(models.TextChoices):
        VC_JOIN = "VC_JOIN", "Joined event VC"
        MANUAL  = "MANUAL",  "Manually checked in"
        AUTO    = "AUTO",    "Auto (Discord interest)"

    event = models.ForeignKey(EventPlan, on_delete=models.CASCADE, related_name="rsvps")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_rsvps",
    )
    status = models.CharField(
        max_length=20, choices=RSVPStatus.choices, db_index=True
    )
    # Which custom RSVP option the user selected (e.g. "Pilot", "Infantry"). Null = standard status.
    rsvp_option = models.ForeignKey(
        "RSVPOption", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="rsvps",
    )
    rsvp_time = models.DateTimeField(default=timezone.now)
    note = models.TextField(max_length=500, blank=True, default="")
    is_anonymous = models.BooleanField(default=False)

    # Check-in
    checked_in = models.BooleanField(default=False)
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_in_source = models.CharField(
        max_length=10, choices=CheckInSource.choices, null=True, blank=True
    )

    # Post-event
    attendance_outcome = models.CharField(
        max_length=20, choices=AttendanceOutcome.choices, null=True, blank=True
    )

    # Reminder dedup — list of minutes_before values already sent
    reminders_sent = models.JSONField(default=list)

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        unique_together = [("event", "user")]
        ordering = ["rsvp_time"]

    def __str__(self):
        return f"{self.user} → {self.event_id} [{self.status}]"


# ---------------------------------------------------------------------------
# EventReminder — configurable per-event reminder
# ---------------------------------------------------------------------------

class EventReminder(models.Model):
    """
    One reminder entry for an event.  Multiple reminders per event allowed.
    The Celery task `send_due_event_reminders` polls for unsent reminders every minute.
    """

    class Target(models.TextChoices):
        DM      = "DM",      "Direct Message all GOING/MAYBE RSVPs"
        CHANNEL = "CHANNEL", "Post in a channel"

    event = models.ForeignKey(EventPlan, on_delete=models.CASCADE, related_name="reminders")
    minutes_before = models.PositiveIntegerField(
        help_text="Send this reminder N minutes before event start"
    )
    target = models.CharField(max_length=10, choices=Target.choices, default=Target.DM)
    channel_id = models.BigIntegerField(
        null=True, blank=True,
        help_text="Discord channel ID for CHANNEL target"
    )
    custom_message = models.TextField(
        blank=True, null=True,
        help_text="Override message body. Leave blank to use the default reminder text."
    )
    title_template = models.CharField(
        max_length=500, blank=True, null=True,
        help_text="Notification title. Supports $EventName, $DT_TimeRelative. Leave blank for default.",
    )
    mentions = models.JSONField(
        default=list, blank=True,
        help_text="Discord role IDs (ints) to mention in this notification.",
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        unique_together = [("event", "minutes_before", "target")]

    def __str__(self):
        return f"{self.event_id} -{self.minutes_before}m [{self.target}]"

    @property
    def send_at(self):
        return self.event.planned_start_at - timedelta(minutes=self.minutes_before)

    @property
    def is_due(self):
        return self.sent_at is None and timezone.now() >= self.send_at


# ---------------------------------------------------------------------------
# EventReport — post-event summary, auto-generated on COMPLETED
# ---------------------------------------------------------------------------

class EventReport(models.Model):
    """
    Auto-generated after an event completes.
    Links back to disfunction.EventAttendance for detailed voice session data.
    """
    plan = models.OneToOneField(EventPlan, on_delete=models.CASCADE, related_name="report")
    actual_start_at = models.DateTimeField(null=True, blank=True)
    actual_end_at = models.DateTimeField(null=True, blank=True)

    # UUID of the linked disfunction.EventAttendance row
    attendance_session_id = models.CharField(max_length=36, null=True, blank=True)
    voice_channel_id = models.BigIntegerField(null=True, blank=True)

    total_rsvp_going = models.PositiveIntegerField(default=0)
    total_checked_in = models.PositiveIntegerField(default=0)
    total_late = models.PositiveIntegerField(default=0)
    total_no_show = models.PositiveIntegerField(default=0)

    lookback_thread_id = models.BigIntegerField(null=True, blank=True)
    summary = models.TextField(blank=True, null=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"Report: {self.plan_id}"


# ---------------------------------------------------------------------------
# TemplateCategory — groups event templates (e.g. FPS, Piloting, Support)
# ---------------------------------------------------------------------------

class TemplateCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        default_permissions = ()
        ordering = ["sort_order", "name"]
        verbose_name = "Template Category"
        verbose_name_plural = "Template Categories"

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# EventTemplate — reusable template for quick event creation
# ---------------------------------------------------------------------------

class EventTemplate(models.Model):
    """
    Staff-defined templates that pre-fill EventPlan fields.
    Permissions control who can use each template.
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    location = models.CharField(max_length=200, blank=True, default="")
    category = models.ForeignKey(
        TemplateCategory,
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name="templates",
        help_text="Group this template under a category (e.g. FPS, Piloting, Support).",
    )
    indicator = models.ForeignKey(
        EventIndicator,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="templates",
    )
    default_duration = models.DurationField(default=timedelta(hours=2))
    default_description = models.TextField(blank=True, default="")
    auto_generate_vc = models.BooleanField(default=True)
    private_thread = models.BooleanField(default=False)
    vc_profile = models.ForeignKey(
        "disfunction.VoiceChannelProfile",
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name="event_templates",
    )
    default_capacity = models.PositiveIntegerField(null=True, blank=True)
    announcement_channel_override_id = models.BigIntegerField(null=True, blank=True)
    # List of ints: minutes before start to send reminders. E.g. [60, 15]
    default_reminders_minutes = models.JSONField(default=list)
    recurring_rule = models.CharField(max_length=512, blank=True, null=True)
    cover_image_url = models.URLField(max_length=512, blank=True, null=True)
    color_hex = models.CharField(max_length=7, blank=True, null=True)
    hide_attendees = models.BooleanField(default=False)
    mentions_on_create = models.JSONField(default=list, blank=True)
    mentions_on_start = models.JSONField(default=list, blank=True)
    mentions_on_vc_unlock = models.JSONField(default=list, blank=True)
    allow_maintain_rsvp = models.BooleanField(default=False)
    thread_title_template = models.CharField(max_length=200, default="$EventName", blank=True)
    thread_auto_archive_duration = models.CharField(max_length=20, default="OneDay", blank=True)
    thread_join_on_rsvp = models.BooleanField(default=True)
    thread_start_message_type = models.CharField(max_length=10, default="Thread", blank=True)
    emoji_going     = models.CharField(max_length=64, default="✅", blank=True)
    emoji_maybe     = models.CharField(max_length=64, default="❓", blank=True)
    emoji_not_going = models.CharField(max_length=64, default="❌", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="event_templates",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ("can_create_event_with", "Can use this template to create events"),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# RSVPOption — custom RSVP choices (server defaults or per-event overrides)
# ---------------------------------------------------------------------------

class RSVPOption(models.Model):
    """
    Custom RSVP option (e.g. Pilot ✈️, Infantry 🪖, Decline ❌).

    guild_id + event=None  →  server-level default shown on every event.
    guild_id + event=<plan> →  per-event override (replaces server defaults for that event).
    maps_to_status maps the option to a system RSVP status for attendance tracking.
    """

    class MapsTo(models.TextChoices):
        GOING     = "GOING",     "Going"
        MAYBE     = "MAYBE",     "Maybe"
        NOT_GOING = "NOT_GOING", "Not Going"

    guild_id = models.BigIntegerField(db_index=True)
    event = models.ForeignKey(
        EventPlan, null=True, blank=True, on_delete=models.CASCADE,
        related_name="rsvp_options",
        help_text="Leave blank for a server-wide default.",
    )
    emoji = models.CharField(max_length=64)
    label = models.CharField(max_length=100)
    maps_to_status = models.CharField(
        max_length=20, choices=MapsTo.choices, default=MapsTo.GOING,
        help_text="Which system attendance bucket this option counts toward.",
    )
    capacity_limit = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Max RSVPs for this option. Null = unlimited.",
    )
    reminders_enabled = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.emoji} {self.label}"


# ---------------------------------------------------------------------------
# EventAttendeeRole — auto Discord role assignment on RSVP / event start
# ---------------------------------------------------------------------------

class EventAttendeeRole(models.Model):
    """
    Automatically assigns a Discord role to RSVP'd members at a configurable time.
    Optionally removes the role when the event ends.
    """

    class AddTime(models.TextChoices):
        ON_RSVP  = "RSVP",        "On RSVP"
        ON_START = "EVENT_START",  "On Event Start"

    event = models.ForeignKey(EventPlan, on_delete=models.CASCADE, related_name="attendee_roles")
    role_id = models.BigIntegerField(help_text="Discord role snowflake ID")
    add_time = models.CharField(
        max_length=15, choices=AddTime.choices, default=AddTime.ON_RSVP,
    )
    remove_on_end = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()
        unique_together = [("event", "role_id")]

    def __str__(self):
        return f"Role {self.role_id} → {self.event_id} [{self.add_time}]"


# ---------------------------------------------------------------------------
# EventRoleRestriction — limit RSVP access to specific Discord roles
# ---------------------------------------------------------------------------

class EventRoleRestriction(models.Model):
    """
    Only members with one of the listed Discord roles may RSVP to this event.
    If rsvp_option is set, the restriction applies only to that option.
    """
    event = models.ForeignKey(EventPlan, on_delete=models.CASCADE, related_name="role_restrictions")
    role_id = models.BigIntegerField(help_text="Discord role snowflake ID allowed to RSVP")
    rsvp_option = models.ForeignKey(
        RSVPOption, null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Restrict to a specific RSVP option. Null = applies to all RSVP.",
    )

    class Meta:
        default_permissions = ()
        unique_together = [("event", "role_id", "rsvp_option")]

    def __str__(self):
        return f"Restrict role {self.role_id} on {self.event_id}"
