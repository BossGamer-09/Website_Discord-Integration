"""
Publish Redis events when a SpecialtyGrant is resolved so the cog can
update the approval embed and post the achievement announcement.
"""
import json
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

PUBSUB_CHANNEL = "app.specialty.notify"


def publish(payload: dict):
    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
        r.publish(PUBSUB_CHANNEL, json.dumps(payload))
    except Exception as exc:
        logger.error("specialty publish error: %s", exc)


@receiver(post_save, sender="specialty.SpecialtyGrant")
def on_specialty_grant_save(sender, instance, created, **kwargs):
    from .models import SpecialtyGrant

    if created:
        publish({
            "action":          "SPECIALTY_GRANT_SUBMITTED",
            "grant_pk":        instance.pk,
            "specialty_key":   instance.specialty_key,
            "specialty_label": instance.specialty_label,
            "category":        instance.category,
            "recipient_id":    instance.recipient_discord_id,
            "requested_by_id": instance.requested_by_discord_id,
        })
        return

    if instance.status == SpecialtyGrant.Status.APPROVED:
        publish({
            "action":              "SPECIALTY_GRANT_APPROVED",
            "grant_pk":            instance.pk,
            "specialty_key":       instance.specialty_key,
            "specialty_label":     instance.specialty_label,
            "category":            instance.category,
            "recipient_id":        instance.recipient_discord_id,
            "resolved_by_id":      instance.resolved_by_discord_id,
            "approval_message_id": instance.approval_channel_message_id,
        })
    elif instance.status == SpecialtyGrant.Status.DENIED:
        publish({
            "action":              "SPECIALTY_GRANT_DENIED",
            "grant_pk":            instance.pk,
            "specialty_key":       instance.specialty_key,
            "specialty_label":     instance.specialty_label,
            "recipient_id":        instance.recipient_discord_id,
            "resolved_by_id":      instance.resolved_by_discord_id,
            "approval_message_id": instance.approval_channel_message_id,
        })
