import json
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)
PUBSUB_CHANNEL = "quartermaster:sync"


def publish(payload: dict):
    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
        r.publish(PUBSUB_CHANNEL, json.dumps(payload))
    except Exception as exc:
        logger.error("quartermaster publish error: %s", exc)


# ── Loot stockpile ────────────────────────────────────────────────────────────

@receiver(post_save, sender="loot_tracker.LootStock")
def on_stock_change(sender, instance, **kwargs):
    publish({
        "action":     "STOCK_UPDATED",
        "item_id":    instance.item_id,
        "item_name":  instance.item.name,
        "quantity":   instance.quantity,
    })


@receiver(post_save, sender="loot_tracker.LootRequest")
def on_loot_request_save(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        discord_id = instance.requester.discorduser.discorduid
    except Exception:
        discord_id = None
    publish({
        "action":                 "REQUEST_SUBMITTED",
        "request_uid":            str(instance.uid),
        "item_name":              instance.item.name,
        "direction":              instance.direction,
        "quantity":               instance.quantity,
        "location":               instance.location,
        "notes":                  instance.notes,
        "requester_discord_id":   str(discord_id) if discord_id else None,
    })


@receiver(post_save, sender="loot_tracker.LootRequest")
def on_loot_request_status_change(sender, instance, created, **kwargs):
    if created:
        return
    if instance.status not in ("APPROVED", "DENIED"):
        return
    try:
        discord_id = instance.requester.discorduser.discorduid
    except Exception:
        discord_id = None
    publish({
        "action":               f"REQUEST_{instance.status}",
        "request_uid":          str(instance.uid),
        "item_name":            instance.item.name,
        "direction":            instance.direction,
        "quantity":             instance.quantity,
        "requester_discord_id": str(discord_id) if discord_id else None,
        "reviewer_note":        instance.reviewer_note,
    })


# ── StockUnit changes → refresh inventory embed ───────────────────────────────

@receiver(post_save, sender="loot_tracker.StockUnit")
def on_stock_unit_change(sender, instance, **kwargs):
    publish({
        "action":  "STOCK_UPDATED",
        "item_id": instance.item_id,
    })


# ── Physical stock (StockAssignment) ─────────────────────────────────────────

@receiver(post_save, sender="loot_tracker.StockAssignment")
def on_stock_assignment(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        unit  = instance.unit
        item  = unit.item
        avail = item.available_count
        publish({
            "action":               "StockAssignment.new",
            "log_action":           instance.action,
            "item_name":            item.name,
            "unit_uid":             str(unit.uid),
            "stats":                unit.stats,
            "recipient":            instance.recipient.display_name if instance.recipient_id else instance.recipient_name,
            "actioned_by":          instance.actioned_by.username if instance.actioned_by_id else "Unknown",
            "notes":                instance.notes,
            "available_after":      avail,
            "low_stock":            item.is_low_stock,
            "low_stock_threshold":  item.low_stock_threshold,
        })
    except Exception as exc:
        logger.error("on_stock_assignment publish error: %s", exc)


# ── Crafted weapons ───────────────────────────────────────────────────────────

@receiver(post_save, sender="loot_tracker.TrackedCraftedWeapon")
def on_crafted_weapon_save(sender, instance, **kwargs):
    publish({"action": "CraftedWeapon.updated", "weapon_id": instance.pk})


@receiver(post_save, sender="loot_tracker.TrackedCraftedWeaponState")
def on_crafted_state_save(sender, instance, **kwargs):
    publish({"action": "CraftedWeaponState.updated", "weapon_id": instance.for_weapon_id})


@receiver(post_save, sender="loot_tracker.TrackedCraftedRequests")
def on_crafted_request_save(sender, instance, **kwargs):
    publish({"action": "CraftedRequest.updated", "weapon_id": instance.for_weapon_id})


# ── Merit requests ────────────────────────────────────────────────────────────

@receiver(post_save, sender="loot_tracker.MeritRequest")
def on_merit_request_save(sender, instance, created, **kwargs):
    from django.conf import settings
    site = getattr(settings, "SITE_URL", "").rstrip("/")

    if created:
        publish({
            "action":       "MERIT_REQUEST_SUBMITTED",
            "request_uid":  str(instance.uid),
            "kind":         instance.kind,
            "title":        instance.title,
            "amount":       instance.amount,
            "detail_url":   f"{site}/loot/merits/{instance.uid}/",
        })
        return

    if instance.status in ("FULFILLED", "DENIED"):
        action = "MERIT_REQUEST_FULFILLED" if instance.status == "FULFILLED" else "MERIT_REQUEST_DENIED"
        try:
            discord_id = instance.requester.discorduser.discorduid
        except Exception:
            discord_id = None
        publish({
            "action":        action,
            "request_uid":   str(instance.uid),
            "kind":          instance.kind,
            "title":         instance.title,
            "amount":        instance.amount,
            "reviewer_note": instance.reviewer_note,
            "discord_id":    str(discord_id) if discord_id else None,
        })
