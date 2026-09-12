import json
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django_redis import get_redis_connection
from .models import TrackedCraftedWeapon, TrackedCraftedWeaponState, TrackedCraftedRequests, StockAssignment


PUBSUB_CHANNEL = "app.inventory.sync"


def publish(payload):
    r = get_redis_connection("default")
    r.publish(PUBSUB_CHANNEL, json.dumps(payload))


@receiver([post_save, post_delete], sender=TrackedCraftedWeapon)
@receiver([post_save, post_delete], sender=TrackedCraftedWeaponState)
@receiver([post_save, post_delete], sender=TrackedCraftedRequests)
def trigger_inventory_refresh(sender, instance, **kwargs):
    pub_refresh_listings(instance)


def pub_refresh_listings(instance):
    if getattr(instance, '_skip_signals', False):
        return

    model_name = instance._meta.object_name
    publish({
        "action": "TrackedCraftedWeapon.refresh",
        "type": f"{model_name}.refresh",
        "id": str(instance.pk)
    })


@receiver(post_save, sender=StockAssignment)
def on_stock_assignment(sender, instance, created, **kwargs):
    if not created:
        return
    if getattr(instance, '_skip_signals', False):
        return

    unit  = instance.unit
    item  = unit.item
    who   = instance.recipient.display_name if instance.recipient_id else instance.recipient_name or "Unknown"

    payload = {
        "action":     "StockAssignment.new",
        "assignment_id": instance.pk,
        "log_action": instance.action,
        "item_name":  item.name,
        "unit_id":    unit.pk,
        "stats":      unit.stats,
        "recipient":  who,
        "notes":      instance.notes,
        "actioned_by": str(instance.actioned_by) if instance.actioned_by_id else None,
        # low-stock check
        "available_after": item.available_count,
        "low_stock":       item.is_low_stock,
        "low_stock_threshold": item.low_stock_threshold,
    }
    publish(payload)
