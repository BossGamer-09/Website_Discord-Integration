import json

from django_redis import get_redis_connection
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver, Signal

from .models import Post, Entry, Tag


PUBSUB_CHANNEL = "app.discordwebcms.sync"


post_internal_order_changed_signal = Signal()


def publish(payload):
    r = get_redis_connection("default")
    r.publish(PUBSUB_CHANNEL, json.dumps(payload))


@receiver(post_internal_order_changed_signal, sender=Post)
def sync_reorder(sender, instance, **kwargs):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "INTERNALREORDER",
        "type": "POST",
        "id": instance.id
    })


@receiver(post_save, sender=Post)
def sync_post_save(sender, instance, created, **kwargs):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "CREATE" if created else "UPDATE",
        "type": "POST",
        "id": instance.id
    })

    if not created and instance.discord_message_id and instance.discord_channel_id:
        publish({
            "action": "UPDATE",
            "type": "LIVE_MSG",
            "post_id": instance.id,
            "channel_id": instance.discord_channel_id,
            "message_id": instance.discord_message_id,
        })


@receiver(post_delete, sender=Post)
def sync_post_delete(sender, instance, **kwargs):
    if getattr(instance, '_skip_signals', False):
        return

    if instance.discord_thread_id:
        publish({
            "action": "DELETE",
            "type": "POST",
            "discord_id": instance.discord_thread_id
        })


@receiver(post_save, sender=Entry)
def sync_entry_save(sender, instance, created, **kwargs):
    if instance.changed_fields_from_db == ["order"]:
        return

    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "UPDATE",
        "type": "POST",
        "id": instance.post.id
    })


@receiver(post_delete, sender=Entry)
def sync_entry_delete(sender, instance, **kwargs):
    if getattr(instance, '_skip_signals', False):
        return

    if instance.discord_message_id:
        publish({
            "action": "DELETE",
            "type": "ENTRY",
            "discord_id": instance.discord_message_id,
            "thread_id": instance.post.discord_thread_id
        })


@receiver(post_save, sender=Tag)
def sync_tag_save(sender, instance, created, **kwargs):
    if getattr(instance, '_skip_signals', False):
        return

    publish({"action": "UPDATE", "type": "TAGS"})
