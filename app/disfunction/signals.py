"""
app/disfunction/signals.py

Publishes Redis events when TemporaryVoiceChannel channel_type changes,
replacing the deprecated asyncio.run_coroutine_threadsafe approach in
orm_models/signals.py.
"""
import json
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from app.unifieduser.signals import publish

from .models import TemporaryVoiceChannel, EventAttendanceRecord

logger = logging.getLogger(__name__)

PROTECTED_TYPES = {"RED", "PROTECTED", "STAFF", "LEADER"}


@receiver(post_save, sender=TemporaryVoiceChannel)
def handle_temp_vc_type_change(sender, instance, created, **kwargs):
    """
    Publishes a Redis event whenever a TemporaryVoiceChannel is created or
    its channel_type changes.  The VoiceBanCog subscribes and applies / removes
    channel-level permission overwrites accordingly.
    """
    new_type = instance.channel_type
    old_type = (
        instance.get_value_loaded_from_db("channel_type")
        if instance.has_value_changed_from_db("channel_type")
        else None
    )

    if created:
        if new_type in PROTECTED_TYPES:
            _publish_vc_event(instance, action="CREATED_PROTECTED")
    else:
        if new_type != old_type:
            if new_type in PROTECTED_TYPES:
                _publish_vc_event(instance, action="CHANGED_TO_PROTECTED")
            elif old_type in PROTECTED_TYPES:
                _publish_vc_event(instance, action="CHANGED_FROM_PROTECTED")


def _publish_vc_event(instance: TemporaryVoiceChannel, action: str) -> None:
    try:
        publish({
            "type": "TempVCTypeChanged",
            "action": action,
            "channel_id": instance.channel_id,
            "guild_id": instance.guild_id,
            "channel_type": instance.channel_type,
            "profile_slug": instance.profile_slug,
        })
    except Exception:
        logger.exception("Failed to publish TempVCTypeChanged event for channel %s", instance.channel_id)


@receiver(post_save, sender=EventAttendanceRecord)
def set_first_event_at(sender, instance, created, **kwargs):
    """Set OrgPlayer.first_event_at the first time a member appears in an attendance record."""
    if not created:
        return
    try:
        from app.discordauth.models import DiscordUser
        from django.utils import timezone as tz
        du = DiscordUser.objects.select_related("user").get(discorduid=instance.user_id)
        player = du.user
        if player and not player.first_event_at:
            player.first_event_at = instance.join_time or tz.now()
            player.save(update_fields=["first_event_at"])
    except DiscordUser.DoesNotExist:
        pass
    except Exception:
        logger.exception("set_first_event_at: error for user_id=%s", instance.user_id)
