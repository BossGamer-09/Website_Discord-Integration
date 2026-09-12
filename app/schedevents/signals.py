"""
app/schedevents/signals.py

Django signals → Redis Pub/Sub IPC so the Discord bot cogs react to
model changes made from Django (admin, web UI, Celery tasks).

Published to channel: "schedevents.sync"

Message types:
  EventPlanStatusChanged  — status field changed (e.g. DRAFT→PUBLISHED, PUBLISHED→ACTIVE)
  EventPlanUpdated        — any other field changed on an EventPlan
  EventRSVPChanged        — an EventRSVP was created, updated, or deleted (status="REMOVED")
  EventReminderDue        — a reminder is now due (published by the Celery task)
"""
import json
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django_redis import get_redis_connection

from app.schedevents.models import EventPlan, EventRSVP

logger = logging.getLogger(__name__)

PUBSUB_CHANNEL = "schedevents.sync"


class _SafeEncoder(json.JSONEncoder):
    """Handles ULID, UUID, Decimal, and other non-standard types."""
    def default(self, obj):
        # ULID / UUID → string
        if hasattr(obj, 'hex') or hasattr(obj, '__str__') and type(obj).__module__ != 'builtins':
            return str(obj)
        return super().default(obj)


def publish(payload: dict):
    try:
        r = get_redis_connection("default")
        r.publish(PUBSUB_CHANNEL, json.dumps(payload, cls=_SafeEncoder))
    except Exception:
        logger.exception("schedevents.signals: failed to publish %s", payload)


# ---------------------------------------------------------------------------
# EventPlan signals
# ---------------------------------------------------------------------------

@receiver(post_save, sender=EventPlan)
def on_event_plan_save(sender, instance: EventPlan, created: bool, **kwargs):
    if getattr(instance, "_skip_signals", False):
        return

    status_before = instance.get_value_loaded_from_db("status") if not created else None
    status_after = instance.status

    if created or status_before != status_after:
        publish({
            "type": "EventPlanStatusChanged",
            "codename_id": instance.codename_id,
            "status": status_after,
            "status_before": status_before,
            "guild_id": instance.guild_id,
            "title": instance.title,
            "planned_start_at": instance.planned_start_at.isoformat() if instance.planned_start_at else None,
            "discord_scheduled_event_id": instance.discord_scheduled_event_id,
            "announcement_channel_id": instance.announcement_channel_id,
            "announcement_message_id": instance.announcement_message_id,
            "auto_generate_vc": instance.auto_generate_vc,
            "vc_profile_slug": instance.vc_profile.slug if instance.vc_profile_id else None,
        })
        return

    # Non-status update — bot may need to refresh the announcement embed
    changed = instance.changed_fields_from_db
    if changed:
        publish({
            "type": "EventPlanUpdated",
            "codename_id": instance.codename_id,
            "guild_id": instance.guild_id,
            "changed_fields": changed,
            "announcement_message_id": instance.announcement_message_id,
            "announcement_channel_id": instance.announcement_channel_id,
        })


@receiver(post_delete, sender=EventPlan)
def on_event_plan_delete(sender, instance: EventPlan, **kwargs):
    publish({
        "type": "EventPlanDeleted",
        "codename_id": instance.codename_id,
        "guild_id": instance.guild_id,
        "discord_scheduled_event_id": instance.discord_scheduled_event_id,
        "announcement_channel_id": instance.announcement_channel_id,
        "announcement_message_id": instance.announcement_message_id,
        "active_vc_channel_id": instance.active_vc_channel_id,
        "attendee_thread_id": instance.attendee_thread_id,
    })


# ---------------------------------------------------------------------------
# EventRSVP signals
# ---------------------------------------------------------------------------

@receiver(post_delete, sender=EventRSVP)
def on_event_rsvp_delete(sender, instance: EventRSVP, **kwargs):
    if getattr(instance, "_skip_signals", False):
        return

    discord_uid = None
    try:
        discord_uid = instance.user.discorduser.discorduid
    except Exception:
        pass

    publish({
        "type": "EventRSVPChanged",
        "codename_id": instance.event_id,
        "guild_id": instance.event.guild_id,
        "user_id": instance.user_id,
        "discord_uid": discord_uid,
        "status": "REMOVED",
        "status_before": instance.status,
        "checked_in": instance.checked_in,
        "announcement_message_id": instance.event.announcement_message_id,
        "announcement_channel_id": instance.event.announcement_channel_id,
        "attendee_thread_id": instance.event.attendee_thread_id,
    })


@receiver(post_save, sender=EventRSVP)
def on_event_rsvp_save(sender, instance: EventRSVP, created: bool, **kwargs):
    if getattr(instance, "_skip_signals", False):
        return

    status_before = instance.get_value_loaded_from_db("status") if not created else None

    discord_uid = None
    try:
        discord_uid = instance.user.discorduser.discorduid
    except Exception:
        pass

    publish({
        "type": "EventRSVPChanged",
        "codename_id": instance.event_id,
        "guild_id": instance.event.guild_id,
        "user_id": instance.user_id,
        "discord_uid": discord_uid,
        "status": instance.status,
        "status_before": status_before,
        "checked_in": instance.checked_in,
        "announcement_message_id": instance.event.announcement_message_id,
        "announcement_channel_id": instance.event.announcement_channel_id,
        "attendee_thread_id": instance.event.attendee_thread_id,
    })
