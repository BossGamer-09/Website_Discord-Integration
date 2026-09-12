from datetime import timedelta

from django.db import models, connections
from django.db.models import Sum, F, ExpressionWrapper, DurationField, Value, DateTimeField, IntegerField, Func
from django.db.models.functions import Greatest, Least
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ObjectDoesNotExist

from simple_history.models import HistoricalRecords

from app.discordauth.models import DiscordUser


class UserVoiceProfile(DiscordUser):
    class Meta:
        proxy = True
        verbose_name = "User Voice History"
        verbose_name_plural = "User Voice Histories"


class LoggedDiscordChannel(models.Model):
    id = models.BigIntegerField(primary_key=True, help_text="Discord Channel ID")
    name = models.CharField(max_length=255)
    channel_type = models.CharField(max_length=50, db_index=True)
    position = models.PositiveIntegerField(blank=True, null=True)
    parent = models.ForeignKey('self', related_name='children', null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    history = HistoricalRecords()

    def __str__(self):
        return f"{self.name} ({self.id})"

    class Meta:
        default_permissions = ()
        ordering = ['position']


class LoggedDiscordMessage(models.Model):
    class Event(models.TextChoices):
        NEW = 'NEW', 'New'
        EDIT = 'CNG', 'Edit'
        DELETE = 'DEL', 'Delete'

    class Moderated(models.TextChoices):
        NO = 'NO', 'No'
        YES = 'YES', 'Yes'
        UNSURE = 'UNK', 'Unsure'

    id = models.BigIntegerField(primary_key=True, help_text="Discord Message ID")
    event = models.CharField(max_length=3, choices=Event.choices, default=Event.NEW, db_index=True)
    moderated = models.CharField(max_length=3, choices=Moderated.choices, default=Moderated.UNSURE, db_index=True)

    author_name = models.CharField(max_length=255)
    author = models.ForeignKey('discordauth.DiscordUser', related_name="author_for_discord_message", null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False)

    @property
    def safe_author(self):
        try:
            return self.author
        except ObjectDoesNotExist:
            return self.author_id

    discord_executor = models.ForeignKey('discordauth.DiscordUser', related_name="executor_for_discord_message", null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False, help_text="Not Known/Logged when a mod deletes attachments, only mostly reliable on mod msg deletes (Discord Limits)")

    @property
    def safe_discord_executor(self):
        try:
            return self.discord_executor
        except ObjectDoesNotExist:
            return self.discord_executor_id

    channel = models.ForeignKey(LoggedDiscordChannel, related_name='logged_messages', on_delete=models.DO_NOTHING, db_constraint=False)
    content = models.TextField()

    attachments = models.JSONField(default=list, blank=True)  # format: [["discord_url", "temp_backup_url"], ...]

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        permissions = [
            ("view_transcripts", "Can export channel message transcripts"),
        ]

    def __str__(self):
        return f"{self.author.user.username} [{len(self.attachments)}]: {self.content[:50]}"


class TranscriptExportLog(models.Model):
    """Audit record of every /transcript export."""
    exporter_discord_id = models.BigIntegerField(db_index=True, help_text="Discord user ID of the staff member who ran /transcript")
    channel_id          = models.BigIntegerField(db_index=True, help_text="Discord channel ID that was exported")
    channel_name        = models.CharField(max_length=255, blank=True)
    message_count       = models.PositiveIntegerField(default=0)
    exported_at         = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        default_permissions = ()
        ordering = ["-exported_at"]
        verbose_name = "Transcript Export Log"
        verbose_name_plural = "Transcript Export Logs"

    def __str__(self):
        return f"Export of #{self.channel_name} by {self.exporter_discord_id} at {self.exported_at:%Y-%m-%d %H:%M}"


class LoggedVoiceStateSnapshot(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True, primary_key=True)
    expected_resolution = models.DurationField()
    compiled = models.BooleanField(default=False)


class LoggedVoiceState(models.Model):
    discorduser = models.ForeignKey('discordauth.DiscordUser', related_name='+', null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False)

    @property
    def safe_discorduser(self):
        try:
            return self.discorduser
        except ObjectDoesNotExist:
            return self.discorduser_id

    channel = models.ForeignKey(LoggedDiscordChannel, related_name='+', null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False)

    @property
    def safe_channel(self):
        try:
            return self.channel
        except ObjectDoesNotExist:
            return self.channel_id

    for_snapshot = models.ForeignKey(LoggedVoiceStateSnapshot, on_delete=models.CASCADE)

    # State flags
    self_mute = models.BooleanField(default=False)
    self_deaf = models.BooleanField(default=False)
    server_mute = models.BooleanField(default=False)
    server_deaf = models.BooleanField(default=False)
    self_stream = models.BooleanField(default=False)
    self_video = models.BooleanField(default=False)



    class Meta:
        default_permissions = ()
        indexes = [
            models.Index(fields=['discorduser', 'for_snapshot']),
        ]


class MySQLTimeStampDiffSeconds(Func):
    """
    Forces MySQL to calculate the difference between two datetimes in seconds.
    Syntax: TIMESTAMPDIFF(SECOND, start_time, end_time)
    """
    template = 'TIMESTAMPDIFF(SECOND, %(expressions)s)'
    output_field = IntegerField()


class CompiledLoggedVoiceState(models.Model):
    discorduser = models.ForeignKey('discordauth.DiscordUser', on_delete=models.DO_NOTHING, db_constraint=False, related_name="compiled_voice_state")

    @property
    def safe_discorduser(self):
        try:
            return self.discorduser
        except ObjectDoesNotExist:
            return self.discorduser_id

    channel = models.ForeignKey(LoggedDiscordChannel, on_delete=models.DO_NOTHING, db_constraint=False, related_name="compiled_voice_state")

    @property
    def safe_channel(self):
        try:
            return self.channel
        except ObjectDoesNotExist:
            return self.channel_id

    interval_start_at = models.DateTimeField(db_index=True)
    interval_stop_at = models.DateTimeField(db_index=True)

    # State flags
    self_mute = models.BooleanField(default=False)
    self_deaf = models.BooleanField(default=False)
    server_mute = models.BooleanField(default=False)
    server_deaf = models.BooleanField(default=False)
    self_stream = models.BooleanField(default=False)
    self_video = models.BooleanField(default=False)

    class Meta:
        default_permissions = ()
        indexes = [
            models.Index(fields=['discorduser', 'interval_start_at', 'interval_stop_at']),
            models.Index(fields=['interval_start_at', 'interval_stop_at']),
        ]

    @classmethod
    def get_time_present(cls, discorduser_id, start_time, end_time, **filters):
        db_alias = cls.objects.db
        vendor = connections[db_alias].vendor

        if vendor == 'postgresql':
            return cls._get_time_present_pgql(discorduser_id, start_time, end_time, **filters)
        elif vendor == 'mysql':
            return cls._get_time_present_mysql(discorduser_id, start_time, end_time, **filters)
        else:
            raise ImproperlyConfigured(f"Unsupported database vendor for CompiledLoggedVoiceState.get_time_present: {vendor}")

    @classmethod
    def _get_time_present_pgql(cls, discorduser_id, start_time, end_time, **filters):
        """
        Calculates the total time a user spent in a voice channel within a specific 
        timeframe, allowing for arbitrary state filtering (e.g., self_mute=False).
        """
        # 1. Filter for records that belong to the user, match the exact filters,
        # and OVERLAP with the requested time window.
        qs = cls.objects.filter(
            discorduser_id=discorduser_id,
            interval_start_at__lt=end_time,   # Started before our window ends
            interval_stop_at__gt=start_time,  # Ended after our window starts
            **filters
        )

        # 2. Clamp the boundaries so we don't count time outside the requested window
        clamped_start = Greatest(F('interval_start_at'), start_time)
        clamped_end = Least(F('interval_stop_at'), end_time)

        # 3. Calculate the duration of these clamped boundaries
        duration_expr = ExpressionWrapper(
            clamped_end - clamped_start,
            output_field=DurationField()
        )

        # 4. Ask the database to sum it all up
        result = qs.aggregate(total_time=Sum(duration_expr))

        # If the user wasn't in voice at all, aggregate returns None. Fallback to 0.
        return result['total_time'] or timedelta()

    @classmethod
    def _get_time_present_mysql(cls, discorduser_id, start_time, end_time, **filters):
        """
        Calculates the total time a user spent in a voice channel within a specific 
        timeframe (MySQL optimized).
        """
        # 1. Filter for intersecting intervals
        qs = cls.objects.filter(
            discorduser_id=discorduser_id,
            interval_start_at__lt=end_time,   
            interval_stop_at__gt=start_time,  
            **filters
        )

        # 2. Clamp boundaries. 
        # (Wrapping the Python datetimes in Value() is strictly required for MySQL 
        # so it doesn't get confused comparing a column to a string literal).
        clamped_start = Greatest(
            F('interval_start_at'), 
            Value(start_time, output_field=DateTimeField())
        )
        clamped_end = Least(
            F('interval_stop_at'), 
            Value(end_time, output_field=DateTimeField())
        )

        # 3. Use our custom MySQL function 
        # Note the order: start comes before end for TIMESTAMPDIFF
        duration_expr = MySQLTimeStampDiffSeconds(clamped_start, clamped_end)

        # 4. Sum the total SECONDS at the database level
        result = qs.aggregate(total_seconds=Sum(duration_expr))

        # 5. Convert back to a clean Python timedelta
        total_seconds = result['total_seconds'] or 0
        return timedelta(seconds=total_seconds)
