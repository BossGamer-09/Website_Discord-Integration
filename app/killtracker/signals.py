"""KillEvent post_save → Redis Pub/Sub → cog dispatches embed."""
import json
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django_redis import get_redis_connection

from app.killtracker.models import KillEvent

logger = logging.getLogger(__name__)
PUBSUB_CHANNEL = "killtracker.events"


def publish(payload: dict):
    try:
        r = get_redis_connection("default")
        r.publish(PUBSUB_CHANNEL, json.dumps(payload, default=str))
    except Exception:
        logger.exception("killtracker: publish failed: %s", payload)


@receiver(post_save, sender=KillEvent)
def on_kill_event(sender, instance: KillEvent, created: bool, **kwargs):
    if not created:
        return
    publish({
        "type": "KillEvent",
        "id": instance.id,
        "killer_name": instance.killer_name,
        "killer_user_id": instance.killer_id,
        "victim": instance.victim,
        "time": instance.time.isoformat(),
        "zone": instance.zone,
        "weapon": instance.weapon,
        "killers_ship": instance.killers_ship,
        "game_mode": instance.game_mode,
        "victim_org": instance.victim_org,
        "victim_org_sid": instance.victim_org_sid,
        "victim_avatar": instance.victim_avatar,
        "victim_enlisted": instance.victim_enlisted,
        "victim_location": instance.victim_location,
        "anonymous": instance.anonymous,
        "incap": instance.incap,
    })
