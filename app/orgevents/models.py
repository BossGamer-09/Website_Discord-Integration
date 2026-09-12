
from functools import cached_property
from datetime import timedelta

import coolname

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from simple_history.models import HistoricalRecords
from django.conf import settings
from django.contrib.auth.models import Group

from django.core.validators import RegexValidator
from django.db import models, IntegrityError, transaction

from app.main.util.db import OriginalStateMixin, SignalEmittingManager


strict_uppercase_alphanumeric = RegexValidator(
    regex=r'^[A-Z0-9]+$',
    message='Only uppercase letters and numbers are allowed. No spaces or special characters.',
    code='invalid_format'
)


class CodeNameIdMixin(models.Model):
    codename_id = models.CharField(max_length=255, primary_key=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.codename_id:
            max_retries = 7
            base_word_count = 2

            for attempt in range(max_retries):
                current_word_count = base_word_count + attempt
                words = coolname.generate(current_word_count)
                self.codename_id = "".join(word.capitalize() for word in words)

                try:
                    with transaction.atomic():
                        kwargs['force_insert'] = True
                        r = super().save(*args, **kwargs)
                    return r

                except IntegrityError:
                    if attempt == max_retries - 1:
                        raise
        else:
            kwargs.pop('force_insert', None)
            super().save(*args, **kwargs)


class EventIndicator(models.Model):
    id = models.CharField(max_length=25, primary_key=True, validators=[strict_uppercase_alphanumeric], editable=False)
    prefix = models.CharField(max_length=16, default='◻️', blank=True)
    name = models.CharField(max_length=200, default='Public')
    description = models.TextField(default=str, blank=True)

    class Meta:
        permissions = [
            ("can_create_events_with", "Can create an event of this indicator."),
        ]


class EventPrefix(models.Model):  # Unscripted OP: etc
    name = models.CharField(max_length=200)
    description = models.TextField(default=str, blank=True)

    class Meta:
        permissions = [
            ("can_create_events_with", "Can create an event of this prefix."),
        ]


class EventAttendanceOption(models.Model):  # yes no maybe
    name = models.CharField(max_length=200)
    emoji = models.CharField(max_length=200, blank=True, null=True, default=None)

    class Meta:
        permissions = [
            ("can_create_events_with", "Can create an event with this attendance option."),
        ]


class EventAvailabilityOption(models.Model):  # FPS/Flex
    name = models.CharField(max_length=200)
    emoji = models.CharField(max_length=200, blank=True, null=True, default=None)

    class Meta:
        permissions = [
            ("can_create_events_with", "Can create an event with this availability option."),
        ]


class EventAttendanceConfig(models.Model):
    name = models.CharField(max_length=200)
    emoji = models.CharField(max_length=200, blank=True, null=True, default=None)
    option = models.ManyToManyField(EventAttendanceOption, related_name='option_for_configs')
    max_slots = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    limit_to_permission_groups = models.ManyToManyField(Group, related_name='attendance_config_limit')
    limit_to_with_vetting_permission_groups = models.ManyToManyField(Group, related_name='attendance_config_vetting_limit')


class EventAvailabilityConfig(models.Model):
    name = models.CharField(max_length=200)
    emoji = models.CharField(max_length=200, blank=True, null=True, default=None)
    option = models.ManyToManyField(EventAvailabilityOption, related_name='option_for_configs')
    max_slots = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    limit_to_permission_groups = models.ManyToManyField(Group, related_name='availability_config_limit')
    limit_to_with_vetting_permission_groups = models.ManyToManyField(Group, related_name='availability_config_vetting_limit')


# TODO: prompt for position, on channel join if not signed up ping user ask what they wanna attend as
# TODO: waiting room
# TODO organic events; can join as organizer, to help and receive waitroom pings etc
# TODO: add to discord logger per channel models then can link with this

class EventPlan(CodeNameIdMixin, models.Model):
    name = models.CharField(max_length=200)
    indicator = models.ForeignKey(EventIndicator, on_delete=models.PROTECT, related_name='events')
    prefix = models.ForeignKey(EventPrefix, on_delete=models.PROTECT, related_name='events')

    description = models.TextField(max_length=2000)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='created_events', on_delete=models.SET_NULL, null=True, blank=True)
    organizers = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='organized_events')

    attendance_options = models.ManyToManyField(EventAttendanceConfig, related_name='config_for_events')
    attendance_options_select_max = models.PositiveSmallIntegerField(default=1)
    availibility_options = models.ManyToManyField(EventAvailabilityConfig, related_name='config_for_events')
    availibility_options_select_max = models.PositiveSmallIntegerField(default=1)

    created_at = models.DateTimeField(null=False, blank=True)
    banner_image = models.FileField(upload_to="uploads/")
    has_discord_event = models.BooleanField(default=True)

    # TODO attendee_thread = models.
    # TODO create_attendee_thread = models.
    # TODO public_thread = models.
    # TODO create_public_thread = models.
    # TODO rememberance_thread = models.
    # TODO create_rememberance_thread = models.

    planned_start_at = models.DateTimeField(null=False, blank=True)
    planned_duration = models.DurationField(default=timedelta(hours=2, minutes=0), null=False, blank=True)

    report = models.OneToOneField("EventReport", related_name='plan', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        permissions = [
            ("can_modify_delete_created", "Can modify events where they are the value for created_by."),
            ("can_modify_delete_organizer", "Can modify events where they are added to the list of organizers."),
        ]


class EventReport(models.Model):
    actual_start_at = models.DateTimeField(null=False, blank=True)
    actual_end_at = models.DateTimeField(null=True, blank=True)

    # TODO lookback_leader_thread = models.
    # TODO related_channels = models.ManyToManyField(EventAttendanceConfig, related_name='config_for_events')


class EventTemplate(models.Model):
    descriptor = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    indicator = models.ForeignKey(EventIndicator, on_delete=models.PROTECT, related_name='template_events')
    prefix = models.ForeignKey(EventPrefix, on_delete=models.PROTECT, related_name='template_events')

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='created_event_templates', on_delete=models.SET_NULL, null=True, blank=True)
    organizers = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='organized_event_templates')

    attendance_options = models.ManyToManyField(EventAttendanceConfig, related_name='template_config_for_events')
    attendance_options_select_max = models.PositiveSmallIntegerField(default=1)
    availibility_options = models.ManyToManyField(EventAvailabilityConfig, related_name='template_config_for_events')
    availibility_options_select_max = models.PositiveSmallIntegerField(default=1)

    created_at = models.DateTimeField(null=False, blank=True)
    banner_image = models.FileField(upload_to="uploads/")
    has_discord_event = models.BooleanField(default=True)

    planned_start_at = models.DateTimeField(null=False, blank=True)
    planned_end_at = models.DateTimeField(null=True, blank=True)

    # TODO create_attendee_thread = models.
    # TODO create_public_thread = models.
    # TODO create_rememberance_thread = models.

    class Meta:
        permissions = [
            ("can_create_event_with", "Can use this template to create events"),
            ("can_set_custom_name", "When using template to create event can set a custom name"),
            ("can_set_custom_duration", "When using template to create event can set a custom duration"),
            ("can_set_custom_banner", "When using template to create event can set a custom banner img"),
            ("can_set_custom_description", "When using template to create event can set a custom description"),
        ]


class OrganicEvent(models.Model):
    """Tracks the state of an active Organic (in-game, impromptu) event."""
    thread_id       = models.BigIntegerField(primary_key=True, help_text="Discord Thread ID")
    embed_message_id = models.BigIntegerField(help_text="Main dashboard message in the parent channel")
    event_type      = models.CharField(max_length=50, help_text="e.g. DEATHWATCH, SCPVP")
    starter_id      = models.BigIntegerField(help_text="Discord User ID of the initiator")

    voice_channel_id = models.BigIntegerField(null=True, blank=True)
    is_active        = models.BooleanField(default=True, db_index=True)
    started_at       = models.DateTimeField(auto_now_add=True)

    summary          = models.TextField(null=True, blank=True)
    select_vc_msg_id = models.BigIntegerField(null=True, blank=True)
    latest_summary_source_hash = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ['-started_at']
        db_table = 'orm_models_organicevent'  # keep existing table name

    def __str__(self):
        return f"{self.event_type} - {self.thread_id} ({'Active' if self.is_active else 'Closed'})"
