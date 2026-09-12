import json
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

GOALS_PUBSUB = "app.goals.notify"


def _publish(payload: dict):
    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
        r.publish(GOALS_PUBSUB, json.dumps(payload))
    except Exception as e:
        logger.error("goals signals publish error: %s", e)


@receiver(post_save, sender="goals.OrgGoal")
def on_org_goal_saved(sender, instance, created, **kwargs):
    if created:
        _publish({
            "action":      "ORG_GOAL_CREATED",
            "goal_pk":     instance.pk,
            "title":       instance.title,
            "description": instance.description,
            "due_date":    instance.due_date.isoformat() if instance.due_date else None,
        })
    elif instance.status in ("COMPLETED", "CANCELLED"):
        _publish({
            "action":   "ORG_GOAL_CLOSED",
            "goal_pk":  instance.pk,
            "title":    instance.title,
            "status":   instance.status,
            "thread_channel_id": instance.thread_channel_id,
            "thread_message_id": instance.thread_message_id,
        })


@receiver(post_save, sender="goals.LeaderGoal")
def on_leader_goal_saved(sender, instance, created, **kwargs):
    if created:
        leader_discord_id = None
        try:
            leader_discord_id = instance.leader.discorduser.discorduid
        except Exception:
            pass

        _publish({
            "action":           "LEADER_GOAL_CREATED",
            "goal_pk":          instance.pk,
            "title":            instance.title,
            "description":      instance.description,
            "due_date":         instance.due_date.isoformat() if instance.due_date else None,
            "leader_discord_id": str(leader_discord_id) if leader_discord_id else None,
        })
    elif instance.status in ("COMPLETED", "CANCELLED"):
        _publish({
            "action":  "LEADER_GOAL_CLOSED",
            "goal_pk": instance.pk,
            "title":   instance.title,
            "status":  instance.status,
            "thread_channel_id": instance.thread_channel_id,
            "thread_message_id": instance.thread_message_id,
        })
