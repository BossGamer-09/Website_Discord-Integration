"""
app/pilotboard/signals.py

Django signals → Redis Pub/Sub IPC for the pilot roster.
Published to channel: "pilotboard.sync"

Message types:
  PilotCreated       — new pilot added
  PilotUpdated       — skills / tags / notes changed
  PilotDeleted       — pilot removed
  GoalCompleted      — a PilotGoal was marked completed
"""
import json
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django_redis import get_redis_connection

from app.pilotboard.models import Pilot, PilotGoal

logger = logging.getLogger(__name__)

PUBSUB_CHANNEL = "pilotboard.sync"


class _SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "__str__") and type(obj).__module__ != "builtins":
            return str(obj)
        return super().default(obj)


def publish(payload: dict):
    try:
        r = get_redis_connection("default")
        r.publish(PUBSUB_CHANNEL, json.dumps(payload, cls=_SafeEncoder))
    except Exception:
        logger.exception("pilotboard: failed to publish to Redis")


# ---------------------------------------------------------------------------
# Pilot signals
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Pilot)
def on_pilot_saved(sender, instance, created, **kwargs):
    if created:
        publish({"type": "PilotCreated", "pilot_id": instance.pk, "name": instance.name})
    else:
        publish({"type": "PilotUpdated", "pilot_id": instance.pk, "name": instance.name})


@receiver(post_delete, sender=Pilot)
def on_pilot_deleted(sender, instance, **kwargs):
    publish({"type": "PilotDeleted", "pilot_id": instance.pk, "name": instance.name})


# ---------------------------------------------------------------------------
# Goal signals
# ---------------------------------------------------------------------------

@receiver(post_save, sender=PilotGoal)
def on_goal_saved(sender, instance, created, update_fields, **kwargs):
    # Only fire GoalCompleted when the completed flag flips to True
    if not created and instance.completed:
        publish({
            "type": "GoalCompleted",
            "pilot_id": instance.pilot_id,
            "pilot_name": instance.pilot.name,
            "goal_id": instance.pk,
            "goal_title": instance.title,
        })
