from django.db import models
from django.utils import timezone
from django.apps import apps

class VoiceSession(models.Model):
    """Tracks voice channel sessions for users."""
    
    user_id = models.BigIntegerField(db_index=True)
    guild_id = models.BigIntegerField(db_index=True)
    channel_id = models.BigIntegerField(db_index=True)
    channel_name = models.CharField(max_length=200, null=True, blank=True)
    channel_type = models.CharField(max_length=50, null=True, blank=True)
    
    # Session timestamps
    join_time = models.DateTimeField()
    leave_time = models.DateTimeField(null=True, blank=True)
    
    # Voice states
    was_muted = models.BooleanField(default=False)
    was_deafened = models.BooleanField(default=False)
    was_streaming = models.BooleanField(default=False)
    was_video = models.BooleanField(default=False)
    
    # Calculated fields
    duration_seconds = models.IntegerField(null=True, blank=True, help_text="Duration in seconds")
    talking_time_seconds = models.IntegerField(default=0, help_text="Time actually talking (not muted/deafened)")
    
    # Session metadata
    ended_normally = models.BooleanField(default=True)
    disconnect_reason = models.CharField(max_length=100, null=True, blank=True)
    
    # Additional tracking
    device_info = models.JSONField(default=dict, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    
    class Meta:
        ordering = ['-join_time']
        indexes = [
            models.Index(fields=['user_id', 'join_time']),
            models.Index(fields=['guild_id', 'channel_id']),
            models.Index(fields=['join_time', 'leave_time']),
        ]
    
    def __str__(self):
        return f"VoiceSession: {self.user_id} in {self.channel_name} ({self.duration_seconds}s)"
    
    def calculate_duration(self):
        """Calculate duration if session has ended."""
        if self.leave_time:
            self.duration_seconds = int((self.leave_time - self.join_time).total_seconds())
            # Only count as talking time if not muted/deafened
            if not self.was_muted and not self.was_deafened:
                self.talking_time_seconds = self.duration_seconds
        return self.duration_seconds
    
    def save(self, *args, **kwargs):
        if self.leave_time:
            self.calculate_duration()
        super().save(*args, **kwargs)

class VoiceActivitySummary(models.Model):
    """Daily/weekly/monthly summaries of voice activity."""
    
    user_id = models.BigIntegerField(db_index=True)
    guild_id = models.BigIntegerField(db_index=True)
    
    # Time period
    period_type = models.CharField(
        max_length=10,
        choices=[('DAILY', 'Daily'), ('WEEKLY', 'Weekly'), ('MONTHLY', 'Monthly'), ('TOTAL', 'Total')],
        default='DAILY'
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    
    # Activity metrics
    total_sessions = models.IntegerField(default=0)
    total_duration_seconds = models.IntegerField(default=0)
    total_talking_seconds = models.IntegerField(default=0)
    unique_channels = models.IntegerField(default=0)
    
    # Average metrics
    avg_session_duration = models.IntegerField(default=0)
    avg_talking_percentage = models.FloatField(default=0.0)
    
    # Peak activity
    peak_concurrent_users = models.IntegerField(default=0)
    most_active_channel = models.BigIntegerField(null=True, blank=True)
    
    class Meta:
        unique_together = ['user_id', 'guild_id', 'period_type', 'period_start']
        indexes = [
            models.Index(fields=['user_id', 'period_type', 'period_start']),
        ]
    
    def __str__(self):
        return f"VoiceSummary: {self.user_id} - {self.period_type} ({self.total_duration_seconds}s)"

class EventAttendance(models.Model):
    """Tracks event attendance with voice activity correlation."""
    
    class EventType(models.TextChoices):
        ORGANIC = 'ORGANIC', 'Organic Event'
        SCHEDULED = 'SCHEDULED', 'Scheduled Event'
        TRAINING = 'TRAINING', 'Training Session'
        MEETING = 'MEETING', 'Organization Meeting'
        SOCIAL = 'SOCIAL', 'Social Gathering'
        OPERATION = 'OPERATION', 'Military Operation'
    
    event_id = models.CharField(max_length=100, db_index=True)
    event_name = models.CharField(max_length=200)
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    
    # Event details
    organizer_id = models.BigIntegerField()
    thread_id = models.BigIntegerField(null=True, blank=True)
    voice_channel_id = models.BigIntegerField(null=True, blank=True)
    
    # Time tracking
    scheduled_start = models.DateTimeField(null=True, blank=True)
    actual_start = models.DateTimeField()
    actual_end = models.DateTimeField(null=True, blank=True)
    
    # Attendance metrics
    total_participants = models.IntegerField(default=0)
    average_attendance_duration = models.IntegerField(default=0)
    
    # Event-specific metadata
    tags = models.JSONField(default=list)
    description = models.TextField(null=True, blank=True)
    
    class Meta:
        ordering = ['-actual_start']
    
    def __str__(self):
        return f"Event: {self.event_name} ({self.event_type})"

class EventAttendanceRecord(models.Model):
    """Individual attendance records for events."""
    
    class AttendanceStatus(models.TextChoices):
        PRESENT = 'PRESENT', 'Present'
        LATE = 'LATE', 'Arrived Late'
        LEFT_EARLY = 'LEFT_EARLY', 'Left Early'
        EXCUSED = 'EXCUSED', 'Excused Absence'
        ABSENT = 'ABSENT', 'Absent'
    
    event = models.ForeignKey(EventAttendance, on_delete=models.CASCADE, related_name='attendees')
    user_id = models.BigIntegerField(db_index=True)
    
    # Attendance tracking
    status = models.CharField(max_length=20, choices=AttendanceStatus.choices, default='PRESENT')
    join_time = models.DateTimeField()
    leave_time = models.DateTimeField(null=True, blank=True)
    
    # Voice activity during event
    voice_session = models.ForeignKey(VoiceSession, on_delete=models.SET_NULL, null=True, blank=True)
    total_talking_seconds = models.IntegerField(default=0)
    was_active_participant = models.BooleanField(default=False)
    
    # Verification
    verified_by = models.BigIntegerField(null=True, blank=True)
    verification_notes = models.TextField(null=True, blank=True)
    
    class Meta:
        unique_together = ['event', 'user_id']
        indexes = [
            models.Index(fields=['event', 'user_id']),
            models.Index(fields=['user_id', 'join_time']),
        ]
    
    def __str__(self):
        return f"Attendance: {self.user_id} at {self.event.event_name}"
    
    @property
    def attendance_duration(self):
        if self.leave_time:
            return int((self.leave_time - self.join_time).total_seconds())
        return 0