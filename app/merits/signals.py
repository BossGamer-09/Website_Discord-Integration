"""
Publish Redis events when a MeritRequest status changes so the Discord
bot can DM the requester.
"""
import json
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

PUBSUB_CHANNEL = "app.merits.notify"


def publish(payload: dict):
    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
        r.publish(PUBSUB_CHANNEL, json.dumps(payload))
    except Exception as exc:
        logger.error("merits publish error: %s", exc)


@receiver(post_save, sender="merits.MeritRequest")
def on_merit_request_save(sender, instance, created, **kwargs):
    from .models import MeritRequest

    if created:
        from django.conf import settings
        site = getattr(settings, "SITE_URL", "").rstrip("/")
        publish({
            "action":       "MERIT_REQUEST_SUBMITTED",
            "request_pk":   instance.pk,
            "kind":         instance.kind,
            "title":        instance.title,
            "requester_pk": str(instance.requester_id),
            "amount":       instance.amount,
            "detail_url":   f"{site}/merits/{instance.pk}/",
        })
        return

    # Status transitions
    if instance.status == MeritRequest.Status.FULFILLED:
        _notify_requester(instance, "MERIT_REQUEST_FULFILLED")
    elif instance.status == MeritRequest.Status.DENIED:
        _notify_requester(instance, "MERIT_REQUEST_DENIED")


def _notify_requester(instance, action: str):
    from django.conf import settings
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    try:
        discord_id = instance.requester.discorduser.discorduid
    except Exception:
        return
    publish({
        "action":        action,
        "request_pk":    instance.pk,
        "kind":          instance.kind,
        "title":         instance.title,
        "amount":        instance.amount,
        "reviewer_note": instance.reviewer_note,
        "discord_id":    str(discord_id),
        "fulfill_url":   f"{site}/merits/{instance.pk}/",
    })
