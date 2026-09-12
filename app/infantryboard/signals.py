"""
app/infantryboard/signals.py

Django signals → Redis Pub/Sub IPC for the infantry roster.
Published to channel: "infantryboard.sync"

Message types:
  SoldierCreated  — new soldier added
  SoldierUpdated  — skills / tags / notes changed
  SoldierDeleted  — soldier removed
  GoalCompleted   — a SoldierGoal was marked completed

Also handles:
  on_user_groups_changed — auto-creates a Soldier entry when a user gains
                           view_infantry_roster via group membership.
"""
import json
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django_redis import get_redis_connection

from app.infantryboard.models import Soldier, SoldierGoal

logger = logging.getLogger(__name__)

PUBSUB_CHANNEL = "infantryboard.sync"


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
        logger.exception("infantryboard: failed to publish to Redis")


# ---------------------------------------------------------------------------
# Soldier signals
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Soldier)
def on_soldier_saved(sender, instance, created, **kwargs):
    if created:
        publish({"type": "SoldierCreated", "soldier_id": instance.pk, "name": instance.name})

        # Ping Infantry Lead channel if enabled
        _maybe_ping_lead(instance)
    else:
        publish({"type": "SoldierUpdated", "soldier_id": instance.pk, "name": instance.name})


@receiver(post_delete, sender=Soldier)
def on_soldier_deleted(sender, instance, **kwargs):
    publish({"type": "SoldierDeleted", "soldier_id": instance.pk, "name": instance.name})


# ---------------------------------------------------------------------------
# Goal signals
# ---------------------------------------------------------------------------

@receiver(post_save, sender=SoldierGoal)
def on_goal_saved(sender, instance, created, update_fields, **kwargs):
    # Only fire GoalCompleted when the completed flag flips to True
    if not created and instance.completed:
        publish({
            "type":         "GoalCompleted",
            "soldier_id":   instance.soldier_id,
            "soldier_name": instance.soldier.name,
            "goal_id":      instance.pk,
            "goal_title":   instance.title,
        })


# ---------------------------------------------------------------------------
# Auto-create on group join
# ---------------------------------------------------------------------------

def on_user_groups_changed(sender, instance, action, pk_set, **kwargs):
    """
    Connected in apps.py to User.groups.through m2m_changed.
    When a user gains a group that grants view_infantry_roster,
    auto-create a blank Soldier entry if InfantryAutoCreateSoldier is ON.
    """
    from django.contrib.auth import get_user_model
    if not isinstance(instance, get_user_model()):
        return

    if action != "post_add" or not pk_set:
        return

    from app.preferences.utils import get_global_preference
    from app.infantryboard.preferences import InfantryAutoCreateSoldier, InfantryNewEntryPingEnabled

    if not get_global_preference(InfantryAutoCreateSoldier.import_path):
        return

    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType
    try:
        ct   = ContentType.objects.get(app_label="infantryboard", model="soldier")
        perm = Permission.objects.get(content_type=ct, codename="view_infantry_roster")
    except Exception:
        return

    if not Group.objects.filter(pk__in=pk_set, permissions=perm).exists():
        return

    if Soldier.objects.filter(org_player=instance).exists():
        return

    display = getattr(instance, "display_name", None) or str(instance)
    soldier = Soldier.objects.create(
        name=display,
        org_player=instance,
    )
    logger.info("infantryboard: auto-created Soldier pk=%s for user %s", soldier.pk, instance)

    if not get_global_preference(InfantryNewEntryPingEnabled.import_path):
        return

    try:
        discord_id = instance.discorduser.discorduid
    except Exception:
        discord_id = None

    publish({
        "type":            "SoldierAutoCreated",
        "soldier_id":      soldier.pk,
        "soldier_name":    soldier.name,
        "discord_user_id": str(discord_id) if discord_id else None,
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _maybe_ping_lead(instance: Soldier):
    from app.preferences.utils import get_global_preference
    from app.infantryboard.preferences import InfantryNewEntryPingEnabled, InfantryAutoLinkOrgPlayer

    # Honour auto-link OFF: clear org_player
    if not get_global_preference(InfantryAutoLinkOrgPlayer.import_path) and instance.org_player_id:
        type(instance).objects.filter(pk=instance.pk).update(org_player=None)
        logger.info("InfantryAutoLinkOrgPlayer=OFF: cleared org_player on Soldier pk=%s", instance.pk)

    if not get_global_preference(InfantryNewEntryPingEnabled.import_path):
        return

    try:
        discord_id = instance.org_player.discorduser.discorduid if instance.org_player_id else None
    except Exception:
        discord_id = None

    publish({
        "type":       "SoldierCreatedPing",
        "soldier_id": instance.pk,
        "name":       instance.name,
        "discord_id": str(discord_id) if discord_id else None,
    })
