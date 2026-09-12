"""
app/quartermaster/cogs/quartermaster_cog.py

Unified Quartermaster Discord interface — all commands under /qm.

Loot stockpile:
  /qm deposit     — member submits a deposit request
  /qm withdraw    — member submits a withdrawal request
  /qm status      — show current loot inventory embed
  /qm set         — QM directly sets stock quantity
  /qm add_item    — QM adds a new trackable loot item

Physical equipment stock:
  /qm add         — add a stock unit (creates item type if new)
  /qm add_item    — also creates physical catalogue entries
  /qm list        — list catalogue with unit counts
  /qm units       — list units for a specific item
  /qm assign      — assign a unit to a member
  /qm return      — mark a unit as returned
  /qm lost        — mark a unit as lost
  /qm stock       — quick stock summary for an item
  /qm member      — view all stock held by a member

Merits / Money:
  /prison_time    — convert SC prison time to a merit request
  Redis: MERIT_REQUEST_SUBMITTED / FULFILLED / DENIED

Weapon tracker:
  /inv_submit_weapon  — QM submits/scans a crafted weapon
  Redis: CraftedWeapon.updated / State.updated / Request.updated
"""
import json
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

import redis.asyncio as aioredis
from django.conf import settings

from app.preferences.utils import aget_global_preference
from app.main.util.discord_command_checks import (
    requires_django_perm,
    _verify_and_cache_generic_permission,
    MissingDjangoPermission,
    AccountNotLinked,
)
from ..preferences import (
    LootInventoryChannelID,
    LootRequestChannelID,
    LootQMGroupIDs,
    LootNotificationsEnabled,
    MeritStaffChannelID,
    MeritNotificationsEnabled,
    StockLogChannelID,
    WeaponInventoryChannelID,
    EquipmentChannelID,
    EquipmentRequestChannelID,
)
from ..signals import PUBSUB_CHANNEL
from app.discordauth.preferences import BotGuildID

log = logging.getLogger(__name__)


def _site_url() -> str:
    from django.conf import settings
    return getattr(settings, "SITE_URL", "https://blightveil.org/").rstrip("/") + "/"

_MANAGE  = "loot_tracker.manage_loot"
_SUBMIT  = "loot_tracker.submit_loot_request"
_VIEW    = "loot_tracker.view_loot"
_QM_MGR  = "loot_tracker.manage_stock"
_QM_ASGN = "loot_tracker.assign_stock"
_QM_VIEW = "loot_tracker.view_stock"

LOCATION_CHOICES = [
    ("STANTON", "Stanton"),
    ("PYRO",    "Pyro"),
    ("NYX",     "Nyx"),
]

CATEGORY_EMOJI = {
    "CURRENCY":        "💰",
    "WIKELO_CURRENCY": "🪙",
    "WIKELO_MATERIAL": "🪨",
    "MONSTER_PART":    "🦷",
    "MILITARY":        "🎖️",
    "COMPONENT":       "⚙️",
    "WEAPON":          "🔫",
    "LEGACY":          "📦",
    "OTHER":           "🔹",
}

KIND_EMOJI = {"MONEY": "💰", "MERIT": "🏅"}

_STATUS_EMOJI = {
    "AVAILABLE": "🟢",
    "ASSIGNED":  "🔵",
    "RETURNED":  "🟡",
    "LOST":      "🔴",
    "RETIRED":   "⚫",
}


# ---------------------------------------------------------------------------
# Sync helpers (loot stockpile)
# ---------------------------------------------------------------------------

@sync_to_async
def _get_active_items():
    from app.quartermaster.models import LootItem
    from django.db.models.functions import Coalesce
    from django.db.models import IntegerField, Value
    return list(
        LootItem.objects.filter(is_active=True)
        .select_related("stock")
        .annotate(current_qty=Coalesce("stock__quantity", Value(0, output_field=IntegerField())))
        .order_by("category", "name")
    )


@sync_to_async
def _get_item(item_id: int):
    from app.quartermaster.models import LootItem
    try:
        return LootItem.objects.select_related("stock").get(pk=item_id)
    except LootItem.DoesNotExist:
        return None


@sync_to_async
def _get_qm_discord_ids(group_ids_str: str) -> list[int]:
    if not group_ids_str:
        return []
    from django.contrib.auth.models import Group
    from app.unifieduser.models import OrgPlayer
    gids = [int(x.strip()) for x in group_ids_str.split(",") if x.strip().isdigit()]
    ids = []
    for player in OrgPlayer.objects.filter(groups__pk__in=gids).distinct():
        try:
            ids.append(player.discorduser.discorduid)
        except Exception:
            pass
    return ids


@sync_to_async
def _get_all_stock():
    from app.quartermaster.models import StockItem, StockUnit
    from django.db.models import Count, Q, Sum
    items = list(
        StockItem.objects.annotate(
            avail=Count("units", filter=Q(units__status=StockUnit.Status.AVAILABLE)),
            total=Count("units", filter=~Q(units__status=StockUnit.Status.RETIRED)),
            scu_total=Sum("units__scu_volume", filter=~Q(units__status=StockUnit.Status.RETIRED)),
        ).prefetch_related("units").order_by("category", "name")
    )
    return items


@sync_to_async
def _create_request(requester_discord_id, item_id, direction, quantity, location, notes):
    from app.quartermaster.models import LootRequest, LootItem
    from app.discordauth.models import DiscordUser
    du = DiscordUser.objects.select_related("user").filter(discorduid=requester_discord_id).first()
    if not du:
        return None, f"No linked account. Log in at {_site_url()} first."
    item = LootItem.objects.filter(pk=item_id, is_active=True).first()
    if not item:
        return None, "Item not found."
    req = LootRequest.objects.create(
        requester=du.user,
        item=item,
        direction=direction,
        quantity=quantity,
        location="" if item.location_agnostic else location,
        notes=notes,
    )
    return req, None


@sync_to_async
def _approve_request(request_id, reviewer_discord_id, note=""):
    from app.quartermaster.models import LootRequest, LootStock
    from app.discordauth.models import DiscordUser
    req = LootRequest.objects.select_related("item", "requester").filter(pk=request_id, status="PENDING").first()
    if not req:
        return None, "Request not found or already processed."
    du = DiscordUser.objects.select_related("user").filter(discorduid=reviewer_discord_id).first()
    stock, _ = LootStock.objects.get_or_create(item=req.item)
    if req.direction == "INPUT":
        stock.quantity += req.quantity
    else:
        stock.quantity = max(0, stock.quantity - req.quantity)
    if du:
        stock.updated_by = du.user
    stock.save()
    req.status = "APPROVED"
    req.reviewer_note = note
    req.reviewed_at = timezone.now()
    if du:
        req.reviewed_by = du.user
    req.save()
    return req, None


@sync_to_async
def _deny_request(request_id, reviewer_discord_id, note=""):
    from app.quartermaster.models import LootRequest
    from app.discordauth.models import DiscordUser
    req = LootRequest.objects.select_related("item", "requester").filter(pk=request_id, status="PENDING").first()
    if not req:
        return None, "Request not found or already processed."
    du = DiscordUser.objects.select_related("user").filter(discorduid=reviewer_discord_id).first()
    req.status = "DENIED"
    req.reviewer_note = note
    req.reviewed_at = timezone.now()
    if du:
        req.reviewed_by = du.user
    req.save()
    return req, None


@sync_to_async
def _qm_set_stock(item_id, quantity, reviewer_discord_id):
    from app.quartermaster.models import LootItem, LootStock
    from app.discordauth.models import DiscordUser
    item = LootItem.objects.filter(pk=item_id).first()
    if not item:
        return None, "Item not found."
    du = DiscordUser.objects.select_related("user").filter(discorduid=reviewer_discord_id).first()
    stock, _ = LootStock.objects.get_or_create(item=item)
    stock.quantity = quantity
    if du:
        stock.updated_by = du.user
    stock.save()
    return stock, None


@sync_to_async
def _save_thread_ids(request_id, thread_id, embed_message_id):
    from app.quartermaster.models import LootRequest
    LootRequest.objects.filter(pk=request_id).update(
        thread_id=thread_id, embed_message_id=embed_message_id
    )


# ---------------------------------------------------------------------------
# Equipment request helpers
# ---------------------------------------------------------------------------

@sync_to_async
def _get_craftable_items():
    """Return list of StockItems that have a blueprint and have available (CRAFTED) units."""
    from app.quartermaster.models import StockItem, StockUnit, CraftBlueprint
    from django.db.models import Count, Q
    items = list(
        StockItem.objects
        .filter(
            tracking_mode=StockItem.TrackingMode.CRAFTED,
            blueprint__isnull=False,
        )
        .annotate(
            avail=Count("units", filter=Q(units__status=StockUnit.Status.AVAILABLE)),
        )
        .select_related("blueprint")
        .order_by("category", "name")
    )
    return items


@sync_to_async
def _create_equipment_request(requester_discord_id: int, item_id: int, notes: str):
    from app.quartermaster.models import StockItem, EquipmentRequest
    from app.discordauth.models import DiscordUser
    du = DiscordUser.objects.select_related("user").filter(discorduid=requester_discord_id).first()
    if not du:
        return None, f"No linked account. Log in at {_site_url()} first."
    item = StockItem.objects.filter(pk=item_id).first()
    if not item:
        return None, "Item not found."
    req = EquipmentRequest.objects.create(requester=du.user, stock_item=item, notes=notes)
    return req, None


@sync_to_async
def _approve_equipment_request(request_id: int, reviewer_discord_id: int, unit_uid: str, note: str):
    from app.quartermaster.models import EquipmentRequest, StockUnit, StockAssignment
    from app.discordauth.models import DiscordUser
    req = EquipmentRequest.objects.select_related("stock_item", "requester").filter(
        pk=request_id, status=EquipmentRequest.Status.PENDING
    ).first()
    if not req:
        return None, "Request not found or already processed."
    du = DiscordUser.objects.select_related("user").filter(discorduid=reviewer_discord_id).first()

    assigned_unit = None
    if unit_uid:
        assigned_unit = StockUnit.objects.filter(uid=unit_uid).first()
        if assigned_unit:
            assigned_unit.status = StockUnit.Status.ASSIGNED
            try:
                from app.unifieduser.models import OrgPlayer
                assigned_unit.current_holder = OrgPlayer.objects.filter(
                    discorduser__discorduid=req.requester.discorduser.discorduid
                ).first()
            except Exception:
                pass
            assigned_unit.save(update_fields=["status", "current_holder", "updated_at"])
            StockAssignment.objects.create(
                unit=assigned_unit,
                action=StockAssignment.Action.ASSIGNED,
                recipient=assigned_unit.current_holder,
                recipient_name=req.requester.display_name if hasattr(req.requester, "display_name") else "",
                notes=f"Equipment request #{req.pk}",
                actioned_by=du.user if du else None,
            )

    req.status        = EquipmentRequest.Status.APPROVED
    req.reviewed_by   = du.user if du else None
    req.reviewer_note = note
    req.reviewed_at   = timezone.now()
    req.assigned_unit = assigned_unit
    req.save()
    return req, None


@sync_to_async
def _deny_equipment_request(request_id: int, reviewer_discord_id: int, note: str):
    from app.quartermaster.models import EquipmentRequest
    from app.discordauth.models import DiscordUser
    req = EquipmentRequest.objects.select_related("stock_item", "requester").filter(
        pk=request_id, status=EquipmentRequest.Status.PENDING
    ).first()
    if not req:
        return None, "Request not found or already processed."
    du = DiscordUser.objects.select_related("user").filter(discorduid=reviewer_discord_id).first()
    req.status        = EquipmentRequest.Status.DENIED
    req.reviewed_by   = du.user if du else None
    req.reviewer_note = note
    req.reviewed_at   = timezone.now()
    req.save()
    return req, None


@sync_to_async
def _save_equipment_thread_ids(request_id: int, thread_id: int, embed_message_id: int):
    from app.quartermaster.models import EquipmentRequest
    EquipmentRequest.objects.filter(pk=request_id).update(
        thread_id=thread_id, embed_message_id=embed_message_id
    )


@sync_to_async
def _get_available_units_for_item(item_id: int):
    from app.quartermaster.models import StockUnit
    return list(
        StockUnit.objects
        .filter(item_id=item_id, status=StockUnit.Status.AVAILABLE)
        .order_by("-added_at")[:25]
    )


@sync_to_async
def _get_equipment_request(request_id: int):
    from app.quartermaster.models import EquipmentRequest
    return EquipmentRequest.objects.select_related(
        "stock_item", "requester", "assigned_unit"
    ).filter(pk=request_id).first()


@sync_to_async
def _check_craftable_stock():
    """Return dict mapping blueprint pk → {mat_name: (have_scu, need_scu, ok)}."""
    from decimal import Decimal
    from app.quartermaster.models import CraftBlueprint, StockUnit, StockItem

    result = {}
    bps = list(
        CraftBlueprint.objects
        .prefetch_related("materials", "constraints")
        .select_related("stock_item")
        .order_by("item_type", "stock_item__name")
    )
    for bp in bps:
        mats = list(bp.materials.order_by("sort_order", "pk"))
        bp_can_craft = bool(mats)
        mat_data = []
        for mat in mats:
            qs = StockUnit.objects.filter(
                item__name__icontains=mat.material_name,
                item__tracking_mode=StockItem.TrackingMode.BULK_SCU,
            ).exclude(status="RETIRED").select_related("item")

            have = Decimal("0")
            for u in qs:
                q = u.sc_metadata.get("ore_quality")
                if mat.min_temp > 0:
                    if q is None:
                        continue
                    try:
                        q_int = int(q)
                    except (ValueError, TypeError):
                        continue
                    if mat.temp_is_floor and q_int < mat.min_temp:
                        continue
                    if not mat.temp_is_floor and q_int != mat.min_temp:
                        continue
                have += u.scu_volume or Decimal("0")

            ok = have >= mat.yield_min
            if not ok:
                bp_can_craft = False
            mat_data.append((mat.material_name, float(have), float(mat.yield_min), ok))

        result[bp.pk] = {
            "item_name": bp.stock_item.name,
            "item_id":   bp.stock_item.pk,
            "can_craft": bp_can_craft,
            "materials": mat_data,
            "item_type": bp.get_item_type_display(),
        }
    return bps, result


# ---------------------------------------------------------------------------
# Craft blueprint helpers
# ---------------------------------------------------------------------------

@sync_to_async
def _get_blueprint_for_item(item_name: str):
    from app.quartermaster.models import StockItem, CraftBlueprint
    item = (
        StockItem.objects
        .filter(name__iexact=item_name)
        .first()
    ) or (
        StockItem.objects
        .filter(name__icontains=item_name)
        .first()
    )
    if not item:
        return None, None
    try:
        bp = (
            CraftBlueprint.objects
            .prefetch_related("materials", "constraints")
            .get(stock_item=item)
        )
        return item, bp
    except CraftBlueprint.DoesNotExist:
        return item, None


async def _loot_item_autocomplete(interaction: discord.Interaction, current: str):
    from app.quartermaster.models import LootItem
    qs = LootItem.objects.filter(is_active=True).order_by("name")
    if current:
        qs = qs.filter(name__icontains=current)
    return [
        app_commands.Choice(name=item.name[:100], value=str(item.pk))
        async for item in qs[:25]
    ]


async def _blueprint_autocomplete(interaction: discord.Interaction, current: str):
    from app.quartermaster.models import CraftBlueprint
    qs = CraftBlueprint.objects.select_related("stock_item").order_by("stock_item__name")
    if current:
        qs = qs.filter(stock_item__name__icontains=current)
    return [
        app_commands.Choice(name=bp.stock_item.name[:100], value=bp.stock_item.name[:100])
        async for bp in qs[:25]
    ]


# ---------------------------------------------------------------------------
# QM stock autocompletes
# ---------------------------------------------------------------------------

async def _stock_item_autocomplete(interaction: discord.Interaction, current: str):
    from app.quartermaster.models import StockItem
    qs = StockItem.objects.order_by("name")
    if current:
        qs = qs.filter(name__icontains=current)
    return [app_commands.Choice(name=i.name[:100], value=str(i.pk)) async for i in qs[:25]]


async def _available_unit_autocomplete(interaction: discord.Interaction, current: str):
    from app.quartermaster.models import StockUnit
    qs = StockUnit.objects.filter(status=StockUnit.Status.AVAILABLE).select_related("item")
    if current:
        qs = qs.filter(item__name__icontains=current)
    choices = []
    async for u in qs[:25]:
        stats_hint = ", ".join(f"{k}: {v}" for k, v in list(u.stats.items())[:2])
        choices.append(app_commands.Choice(name=f"#{u.pk} {u.item.name} — {stats_hint}"[:100], value=str(u.uid)))
    return choices


async def _assigned_unit_autocomplete(interaction: discord.Interaction, current: str):
    from app.quartermaster.models import StockUnit
    qs = StockUnit.objects.filter(status=StockUnit.Status.ASSIGNED).select_related("item", "current_holder")
    if current:
        qs = qs.filter(item__name__icontains=current)
    choices = []
    async for u in qs[:25]:
        holder = u.current_holder.display_name if u.current_holder_id else "Unknown"
        choices.append(app_commands.Choice(name=f"#{u.pk} {u.item.name} → {holder}"[:100], value=str(u.uid)))
    return choices


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def _build_request_embed(req, color=discord.Color.yellow()) -> discord.Embed:
    direction_label = "📥 Input Request" if req.direction == "INPUT" else "📤 Withdraw Request"
    embed = discord.Embed(title=direction_label, color=color, timestamp=timezone.now())
    embed.add_field(name="Item",     value=req.item.name,        inline=True)
    embed.add_field(name="Quantity", value=f"{req.quantity:,}",  inline=True)
    embed.add_field(name="Location", value=req.location_display, inline=True)
    try:
        embed.add_field(name="Requested by", value=f"<@{req.requester.discorduser.discorduid}>", inline=True)
    except Exception:
        embed.add_field(name="Requested by", value=str(req.requester), inline=True)
    if req.notes:
        embed.add_field(name="Notes", value=req.notes, inline=False)
    embed.set_footer(text=f"Request #{req.pk} · {req.get_status_display()}")
    return embed


def _fmt_auec(v: int) -> str:
    s = f"{v:,}"
    if v >= 1_000_000_000:
        return f"{s} ({v / 1_000_000_000:.2f} Billion)"
    if v >= 1_000_000:
        return f"{s} ({v / 1_000_000:.2f} Million)"
    if v >= 10_000:
        return f"{s} ({v / 1_000:.1f}K)"
    return s


def _fmt_prison_time(v: int) -> str:
    s = f"{v:,}"
    if v == 0:
        return s
    h = v // 3600
    m = (v % 3600) // 60
    suffix = f"{h}h {m:02d}m" if h else f"{m}m"
    return f"{s} ({suffix})"


_CAT_EMOJI = {
    "CURRENCY":        "💰",
    "WIKELO_CURRENCY": "🪙",
    "WIKELO_MATERIAL": "🪨",
    "MONSTER_PART":    "🦷",
    "MILITARY":        "🎖️",
    "COMPONENT":       "⚙️",
    "WEAPON":          "🔫",
    "LEGACY":          "📦",
}

# Preferred category display order
_CAT_ORDER = ["CURRENCY", "WIKELO_CURRENCY", "WIKELO_MATERIAL", "MONSTER_PART",
              "MILITARY", "COMPONENT", "WEAPON", "LEGACY"]


def _build_stockpile_embed(loot_items, bot_user) -> discord.Embed:
    from collections import defaultdict

    embed = discord.Embed(
        title="📦 BlightVeil Loot Stockpile",
        description=(
            "Click **Deposit** or **Withdraw** below to submit a request.\n"
            f"[Track your request status →]({_site_url()}loot/requests/)"
        ),
        color=0x5865F2,
        timestamp=timezone.now(),
    )
    if bot_user:
        embed.set_footer(
            text="Updates automatically · BlightVeil Loot Tracker",
            icon_url=bot_user.display_avatar.url,
        )

    by_cat: dict = defaultdict(list)
    for item in loot_items:
        by_cat[item.category].append(item)

    # Emit in preferred order, then any unknown categories alphabetically
    ordered = [c for c in _CAT_ORDER if c in by_cat]
    ordered += sorted(c for c in by_cat if c not in _CAT_ORDER)

    for cat_key in ordered:
        cat_items = by_cat[cat_key]
        emoji     = _CAT_EMOJI.get(cat_key, "🔹")
        cat_label = cat_items[0].get_category_display()
        lines = []
        for item in cat_items:
            qty      = getattr(item, "current_qty", 0) or 0
            name     = item.name
            is_merit = "merit" in name.lower()
            is_auec  = cat_key == "CURRENCY" and not is_merit

            qty_str = _fmt_auec(qty) if is_auec else (_fmt_prison_time(qty) if is_merit else f"{qty:,}")

            tgt = item.target_qty or 0
            if item.excess_threshold and qty >= item.excess_threshold:
                dot = "🔵"
            elif item.low_threshold and qty <= (item.low_threshold or 0):
                dot = "🔴"
            elif tgt and qty >= tgt:
                dot = "🟢"
            elif tgt:
                dot = "🟡"
            else:
                dot = "⚪"

            loc  = "" if item.location_agnostic else f"  ·  *{item.get_location_display()}*"
            tgt_str = f" / {tgt:,}" if tgt else ""
            lines.append(f"{dot} **{name}**{loc}\n> {qty_str}{tgt_str}")

        if lines:
            embed.add_field(
                name=f"{emoji} {cat_label}",
                value="\n".join(lines),
                inline=len(lines) <= 3,
            )

    if not loot_items:
        embed.description = (
            "No items configured yet.\n"
            "QMs can add items via `/qm additem` or the admin panel."
        )
    return embed


def _build_inventory_embed(items, bot_user) -> discord.Embed:
    """Embed for equipment inventory — shows StockItem physical gear."""
    from app.quartermaster.models import StockItem
    embed = discord.Embed(
        title="🗃️ QM Equipment Inventory",
        description="🟢 Available · 🟡 Low stock · ⛏️ Bulk SCU · ⚒️ Crafted",
        color=discord.Color.blurple(),
        timestamp=timezone.now(),
    )
    embed.set_footer(
        text="BlightVeil Loot Tracker",
        icon_url=bot_user.display_avatar.url if bot_user else None,
    )
    from collections import defaultdict
    by_cat = defaultdict(list)
    for item in items:
        by_cat[item.category or "Other"].append(item)

    for cat, cat_items in sorted(by_cat.items()):
        emoji = CATEGORY_EMOJI.get(cat, "🔹")
        lines = []
        for item in cat_items:
            avail = getattr(item, "avail", 0) or 0
            total = getattr(item, "total", 0) or 0
            low   = avail <= item.low_stock_threshold

            if item.tracking_mode == StockItem.TrackingMode.BULK_SCU:
                scu = getattr(item, "scu_total", None) or 0
                status = "🟡" if low else "⛏️"
                qualities = [
                    int(u.sc_metadata["ore_quality"])
                    for u in item.units.all()
                    if u.sc_metadata.get("ore_quality") is not None
                    and u.status != "RETIRED"
                ]
                q_str = ""
                if qualities:
                    lo, hi = min(qualities), max(qualities)
                    q_str = f" · Q: {lo}" if lo == hi else f" · Q: {lo}–{hi}"
                lines.append(f"{status} **{item.name}**: {float(scu):.3f} SCU ({total} containers{q_str})")
            elif item.tracking_mode == StockItem.TrackingMode.CRAFTED:
                status = "🟡" if low else "⚒️"
                lines.append(f"{status} **{item.name}**: {avail} available / {total} total")
            else:
                status = "🟡" if low else "🟢"
                lines.append(f"{status} **{item.name}**: {avail} available / {total} total")

        if lines:
            embed.add_field(name=f"{emoji} {cat}", value="\n".join(lines), inline=False)

    if not items:
        embed.description = "No equipment in stock. QMs can add items via `/qm add`."
    return embed


def _build_equipment_embed(items, bot_user) -> discord.Embed:
    """Embed listing craftable StockItems for member equipment requests."""
    embed = discord.Embed(
        title="⚒️ BlightVeil Equipment Panel",
        description=(
            "Click **Request Equipment** to request a crafted item.\n"
            f"[View your requests →]({_site_url()}loot/equipment/requests/)"
        ),
        color=0x7C3AED,
        timestamp=timezone.now(),
    )
    if bot_user:
        embed.set_footer(text="BlightVeil Equipment Tracker", icon_url=bot_user.display_avatar.url)

    from collections import defaultdict
    from app.quartermaster.models import StockUnit
    by_cat = defaultdict(list)
    for item in items:
        by_cat[item.category or "Equipment"].append(item)

    for cat, cat_items in sorted(by_cat.items()):
        lines = []
        for item in cat_items:
            avail = getattr(item, "avail", 0) or 0
            low   = avail <= item.low_stock_threshold
            dot   = "🟡" if low else "🟢"
            lines.append(f"{dot} **{item.name}** — {avail} available")
        if lines:
            embed.add_field(name=cat, value="\n".join(lines), inline=False)

    if not items:
        embed.description = "No craftable equipment in stock. QMs can add items via `/qm add`."
    return embed


# ---------------------------------------------------------------------------
# Equipment request flow
# ---------------------------------------------------------------------------

class EquipmentRequestModal(discord.ui.Modal):
    notes = discord.ui.TextInput(
        label="Notes (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=400,
        placeholder="What will you use this for? Any preferences?",
    )

    def __init__(self, item_id: int, item_name: str, cog):
        super().__init__(title=f"Request — {item_name}"[:45])
        self.item_id   = item_id
        self.item_name = item_name
        self.cog       = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        req, err = await _create_equipment_request(
            interaction.user.id, self.item_id, self.notes.value or ""
        )
        if err or not req:
            await interaction.followup.send(f"❌ {err or 'Could not create request.'}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ Request **#{req.pk}** submitted for **{self.item_name}** — a QM will review it shortly.",
            ephemeral=True,
        )
        await self.cog._open_equipment_request_thread(interaction.guild, req)


class _EquipmentItemSelect(discord.ui.View):
    """Paginated item select for the equipment panel."""

    def __init__(self, items, cog, page: int = 0):
        super().__init__(timeout=120)
        self.all_items = items
        self.cog       = cog
        self.page      = page
        self._build(page)

    def _build(self, page: int):
        self.clear_items()
        chunk = self.all_items[page * 25:(page + 1) * 25]
        sel = discord.ui.Select(
            placeholder="Select the item you want to request...",
            options=[
                discord.SelectOption(
                    label=item.name[:100],
                    description=f"{getattr(item, 'avail', 0) or 0} available"[:100],
                    value=str(item.pk),
                )
                for item in chunk
            ],
        )
        sel.callback = self._on_item
        self.add_item(sel)
        if page > 0:
            btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            btn.callback = self._prev
            self.add_item(btn)
        if (page + 1) * 25 < len(self.all_items):
            btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
            btn.callback = self._next
            self.add_item(btn)

    async def _on_item(self, interaction: discord.Interaction):
        item_id = int(interaction.data["values"][0])
        item    = next((i for i in self.all_items if i.pk == item_id), None)
        if not item:
            await interaction.response.send_message("❌ Item not found.", ephemeral=True)
            return
        await interaction.response.send_modal(EquipmentRequestModal(item_id, item.name, self.cog))

    async def _prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._build(self.page)
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page += 1
        self._build(self.page)
        await interaction.response.edit_message(view=self)


class EquipmentPanelView(discord.ui.View):
    """Persistent 'Request Equipment' button on the equipment channel embed."""

    def __init__(self, cog=None):
        super().__init__(timeout=None)
        self.cog = cog

    async def _get_cog(self, interaction: discord.Interaction):
        return self.cog or interaction.client.cogs.get("Loot Tracker")

    @discord.ui.button(label="⚒️ Request Equipment", style=discord.ButtonStyle.primary,
                       custom_id="equip_panel_request")
    async def request_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        from app.main.util.discord_command_checks import (
            _verify_and_cache_generic_permission, MissingDjangoPermission, AccountNotLinked,
        )
        try:
            await _verify_and_cache_generic_permission(interaction.user.id, _SUBMIT)
        except AccountNotLinked:
            await interaction.response.send_message(
                "🎮 Your Discord account isn't linked to an org profile yet.", ephemeral=True
            )
            return
        except MissingDjangoPermission as e:
            await interaction.response.send_message(
                f"🛡️ **Permission denied:** you need `{e.missing_perm}`.", ephemeral=True
            )
            return

        cog   = await self._get_cog(interaction)
        items = await _get_craftable_items()
        if not items:
            await interaction.response.send_message(
                "❌ No craftable equipment in stock right now.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "⚒️ Select the item you want to **request**:",
            view=_EquipmentItemSelect(items, cog),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Equipment request approval/denial
# ---------------------------------------------------------------------------

class _EquipApproveModal(discord.ui.Modal, title="Approve Equipment Request"):
    unit_uid = discord.ui.TextInput(
        label="Unit UID to assign (optional)",
        required=False,
        max_length=40,
        placeholder="Leave blank to approve without assigning a specific unit",
    )
    note = discord.ui.TextInput(
        label="Note for the member (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=400,
    )

    def __init__(self, request_id: int, cog):
        super().__init__()
        self.request_id = request_id
        self.cog        = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        req, err = await _approve_equipment_request(
            self.request_id, interaction.user.id,
            self.unit_uid.value.strip(), self.note.value or ""
        )
        if err or not req:
            await interaction.followup.send(f"❌ {err or 'Unknown error.'}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ Equipment request **#{req.pk}** approved.", ephemeral=True
        )
        try:
            enabled = await aget_global_preference(LootNotificationsEnabled.import_path)
            if enabled:
                discord_id = req.requester.discorduser.discorduid
                user = await interaction.client.fetch_user(int(discord_id))
                embed = discord.Embed(
                    title=f"✅ Equipment Request Approved: {req.stock_item.name}",
                    description=self.note.value or "Your request has been approved!",
                    color=discord.Color.green(),
                )
                if req.assigned_unit:
                    stats = ", ".join(f"{k}: {v}" for k, v in req.assigned_unit.stats.items()) or "No stats"
                    embed.add_field(name="Assigned Unit", value=f"#{req.assigned_unit.pk} — {stats}", inline=False)
                await user.send(embed=embed)
        except Exception:
            pass
        try:
            if hasattr(interaction.channel, "edit"):
                await interaction.channel.edit(locked=True, archived=True)
        except Exception:
            pass


class _EquipDenyModal(discord.ui.Modal, title="Deny Equipment Request"):
    note = discord.ui.TextInput(
        label="Reason for denial (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=400,
    )

    def __init__(self, request_id: int, cog):
        super().__init__()
        self.request_id = request_id
        self.cog        = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        req, err = await _deny_equipment_request(
            self.request_id, interaction.user.id, self.note.value or ""
        )
        if err or not req:
            await interaction.followup.send(f"❌ {err or 'Unknown error.'}", ephemeral=True)
            return
        await interaction.followup.send(
            f"❌ Equipment request **#{req.pk}** denied.", ephemeral=True
        )
        try:
            enabled = await aget_global_preference(LootNotificationsEnabled.import_path)
            if enabled:
                discord_id = req.requester.discorduser.discorduid
                user = await interaction.client.fetch_user(int(discord_id))
                embed = discord.Embed(
                    title=f"❌ Equipment Request Denied: {req.stock_item.name}",
                    description=self.note.value or "Your request has been denied.",
                    color=discord.Color.red(),
                )
                await user.send(embed=embed)
        except Exception:
            pass
        try:
            if hasattr(interaction.channel, "edit"):
                await interaction.channel.edit(locked=True, archived=True)
        except Exception:
            pass


class EquipmentRequestActionView(discord.ui.View):
    """Approve / Deny buttons posted in each equipment request thread."""

    def __init__(self, request_id: int, cog=None):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.cog        = cog

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success,
                       custom_id="equip_req_approve")
    async def approve_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        try:
            await _verify_and_cache_generic_permission(interaction.user.id, _QM_ASGN)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_modal(_EquipApproveModal(self.request_id, self.cog))

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger,
                       custom_id="equip_req_deny")
    async def deny_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        try:
            await _verify_and_cache_generic_permission(interaction.user.id, _QM_ASGN)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_modal(_EquipDenyModal(self.request_id, self.cog))


# ---------------------------------------------------------------------------
# Request approval/denial views
# ---------------------------------------------------------------------------

class RequestActionView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.request_id = request_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="loot_approve")
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_DenyReasonModal(request_id=self.request_id, approving=True))

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="loot_deny")
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_DenyReasonModal(request_id=self.request_id, approving=False))


class _DenyReasonModal(discord.ui.Modal):
    reason = discord.ui.TextInput(
        label="Note (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder="Add a note for the requester...",
    )

    def __init__(self, request_id: int, approving: bool):
        super().__init__(title="Approve Request" if approving else "Deny Request")
        self.request_id = request_id
        self.approving  = approving

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        note = self.reason.value or ""

        if self.approving:
            req, err = await _approve_request(self.request_id, interaction.user.id, note)
        else:
            req, err = await _deny_request(self.request_id, interaction.user.id, note)

        if err or not req:
            await interaction.followup.send(f"❌ {err or 'Unknown error'}", ephemeral=True)
            return

        color = discord.Color.green() if self.approving else discord.Color.red()
        embed = _build_request_embed(req, color=color)
        try:
            msg = await interaction.channel.fetch_message(req.embed_message_id)
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass

        label = "✅ Approved" if self.approving else "❌ Denied"
        await interaction.followup.send(f"{label} request #{req.pk}.", ephemeral=True)

        try:
            enabled = await aget_global_preference(LootNotificationsEnabled.import_path)
            if enabled:
                discord_id = req.requester.discorduser.discorduid
                user = await interaction.client.fetch_user(int(discord_id))
                dm_embed = _build_request_embed(req, color=color)
                dm_embed.title = f"{'✅ Request Approved' if self.approving else '❌ Request Denied'}: {req.item.name}"
                await user.send(embed=dm_embed)
        except Exception:
            pass

        try:
            if hasattr(interaction.channel, "edit"):
                await interaction.channel.edit(locked=True, archived=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Persistent inventory panel view (buttons on the live channel embed)
# ---------------------------------------------------------------------------

class InventoryPanelView(discord.ui.View):
    """Persistent Deposit / Withdraw buttons attached to the live stockpile embed."""

    def __init__(self, cog=None):
        super().__init__(timeout=None)
        self.cog = cog

    async def _get_cog(self, interaction: discord.Interaction):
        if self.cog:
            return self.cog
        return interaction.client.cogs.get("Loot Tracker")

    async def _check_perm(self, interaction: discord.Interaction, codename: str) -> bool:
        from app.main.util.discord_command_checks import (
            _verify_and_cache_generic_permission, MissingDjangoPermission, AccountNotLinked
        )
        try:
            await _verify_and_cache_generic_permission(interaction.user.id, codename)
            return True
        except AccountNotLinked:
            await interaction.response.send_message(
                "🎮 Your Discord account isn't linked to an org profile yet.", ephemeral=True
            )
        except MissingDjangoPermission as e:
            await interaction.response.send_message(
                f"🛡️ **Permission denied:** you need `{e.missing_perm}`.", ephemeral=True
            )
        return False

    @discord.ui.button(label="📥 Deposit", style=discord.ButtonStyle.primary,
                       custom_id="inv_panel_deposit")
    async def deposit_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._check_perm(interaction, _SUBMIT):
            return
        cog = await self._get_cog(interaction)
        items = await _get_active_items()
        if not items:
            await interaction.response.send_message("❌ No items configured yet.", ephemeral=True)
            return
        await interaction.response.send_message(
            "📥 Select the item you want to **deposit**:",
            view=_ItemSelect(items, "INPUT", cog),
            ephemeral=True,
        )

    @discord.ui.button(label="📤 Withdraw", style=discord.ButtonStyle.danger,
                       custom_id="inv_panel_withdraw")
    async def withdraw_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._check_perm(interaction, _SUBMIT):
            return
        cog = await self._get_cog(interaction)
        items = await _get_active_items()
        if not items:
            await interaction.response.send_message("❌ No items configured yet.", ephemeral=True)
            return
        await interaction.response.send_message(
            "📤 Select the item you want to **withdraw**:",
            view=_ItemSelect(items, "WITHDRAW", cog),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Loot request flow:  item select → location (if needed) → qty modal → thread
# ---------------------------------------------------------------------------

class _RequestModal(discord.ui.Modal):
    qty_input = discord.ui.TextInput(
        label="Quantity",
        style=discord.TextStyle.short,
        required=True,
        max_length=20,
        placeholder="e.g. 1,000,000",
    )
    notes_input = discord.ui.TextInput(
        label="Notes (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=400,
    )

    def __init__(self, item_id: int, direction: str, location: str, item_name: str, cog):
        label = "📥 Deposit" if direction == "INPUT" else "📤 Withdraw"
        super().__init__(title=f"{label} — {item_name}"[:45])
        self.item_id   = item_id
        self.direction = direction
        self.location  = location
        self.cog       = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        raw = self.qty_input.value.replace(",", "").replace("_", "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await interaction.followup.send("❌ Quantity must be a positive whole number.", ephemeral=True)
            return
        req, err = await _create_request(
            interaction.user.id, self.item_id, self.direction,
            int(raw), self.location, self.notes_input.value or "",
        )
        if err or not req:
            await interaction.followup.send(f"❌ {err or 'Could not create request.'}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ Request **#{req.pk}** submitted — a Quartermaster will review it shortly.",
            ephemeral=True,
        )
        await self.cog._open_request_thread(interaction.guild, req)


class _LocationSelect(discord.ui.View):
    def __init__(self, item_id: int, item_name: str, direction: str, cog):
        super().__init__(timeout=120)
        self.item_id   = item_id
        self.item_name = item_name
        self.direction = direction
        self.cog       = cog
        sel = discord.ui.Select(
            placeholder="📍 Which location?",
            options=[discord.SelectOption(label=label, value=value) for value, label in LOCATION_CHOICES],
        )
        sel.callback = self._on_location
        self.add_item(sel)

    async def _on_location(self, interaction: discord.Interaction):
        location = interaction.data["values"][0]
        await interaction.response.send_modal(
            _RequestModal(self.item_id, self.direction, location, self.item_name, self.cog)
        )


class _ItemSelect(discord.ui.View):
    """Paginated item dropdown — 25 items per page."""

    def __init__(self, items, direction: str, cog, page: int = 0):
        super().__init__(timeout=120)
        self.all_items = items
        self.direction = direction
        self.cog       = cog
        self.page      = page
        self._build(page)

    def _build(self, page: int):
        self.clear_items()
        chunk = self.all_items[page * 25:(page + 1) * 25]
        sel = discord.ui.Select(
            placeholder="Select an item...",
            options=[
                discord.SelectOption(
                    label=item.name[:100],
                    description=item.get_category_display()[:100],
                    value=str(item.pk),
                )
                for item in chunk
            ],
        )
        sel.callback = self._on_item
        self.add_item(sel)
        if page > 0:
            btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            btn.callback = self._prev
            self.add_item(btn)
        if (page + 1) * 25 < len(self.all_items):
            btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
            btn.callback = self._next
            self.add_item(btn)

    async def _on_item(self, interaction: discord.Interaction):
        item_id = int(interaction.data["values"][0])
        item    = await _get_item(item_id)
        if not item:
            await interaction.response.send_message("❌ Item not found.", ephemeral=True)
            return
        if item.location_agnostic:
            await interaction.response.send_modal(
                _RequestModal(item_id, self.direction, "", item.name, self.cog)
            )
        else:
            await interaction.response.edit_message(
                content=f"📍 **{item.name}** — which location?",
                view=_LocationSelect(item_id, item.name, self.direction, self.cog),
            )

    async def _prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._build(self.page)
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page += 1
        self._build(self.page)
        await interaction.response.edit_message(view=self)


# ---------------------------------------------------------------------------
# QM stock modals/views
# ---------------------------------------------------------------------------

CRAFT_STATE_CHOICES = [
    app_commands.Choice(name="Available for Knights",    value="HELD_K"),
    app_commands.Choice(name="Available for Org",        value="HELD_O"),
    app_commands.Choice(name="Not under Org control",    value="EXTERNAL"),
]


class UnitStatsModal(discord.ui.Modal, title="Add Stock Unit — Stats"):
    stats_input = discord.ui.TextInput(
        label="Stats (one per line: Name: Value)",
        style=discord.TextStyle.paragraph,
        required=False,
        placeholder="Impact Force: +24.09%\nRecoil Kick: -38.72%",
    )
    condition_notes = discord.ui.TextInput(
        label="Condition Notes (optional)",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, item_id: int, cog, prefill_stats: dict = None, craft_state: str = None):
        super().__init__()
        self.item_id     = item_id
        self.cog         = cog
        self.craft_state = craft_state
        if prefill_stats:
            self.stats_input.default = "\n".join(f"{k}: {v}" for k, v in prefill_stats.items())

    async def on_submit(self, interaction: discord.Interaction):
        from app.quartermaster.models import StockUnit
        from app.discordauth.models import DiscordUser

        stats = {}
        for line in self.stats_input.value.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                stats[k.strip()] = v.strip()

        craft_state = self.craft_state

        def _create():
            du, _ = DiscordUser.ensure_with_user(
                discorduid=interaction.user.id,
                access_token=None, refresh_token=None, access_token_expires=None,
            )
            unit = StockUnit.objects.create(
                item_id=self.item_id,
                stats=stats,
                condition_notes=self.condition_notes.value or "",
                craft_state=craft_state,
                added_by=du.user,
            )
            return unit, unit.item.name

        unit, item_name = await sync_to_async(_create)()
        stats_lines = "\n".join(f"• **{k}**: {v}" for k, v in stats.items()) or "*No stats entered*"
        embed = discord.Embed(title="✅ Unit Added to Stock", description=f"**Unit #{unit.pk}** added to **{item_name}**", color=0x22C55E)
        embed.add_field(name="Stats", value=stats_lines, inline=False)
        if craft_state:
            embed.add_field(name="State", value=dict(StockUnit.CraftState.choices).get(craft_state, craft_state), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BulkSCUModal(discord.ui.Modal, title="Add Bulk / SCU Item"):
    scu_input = discord.ui.TextInput(
        label="SCU Volume",
        style=discord.TextStyle.short,
        required=True,
        placeholder="e.g. 818.0",
        max_length=12,
    )
    ore_quality = discord.ui.TextInput(
        label="Ore Quality (optional)",
        style=discord.TextStyle.short,
        required=False,
        placeholder="0 – 1000 (e.g. 750)",
        max_length=50,
    )
    source_location = discord.ui.TextInput(
        label="Source Location (optional)",
        style=discord.TextStyle.short,
        required=False,
        placeholder="e.g. Pyro — Bloom, Stanton — ARC-L1",
        max_length=100,
    )
    condition_notes = discord.ui.TextInput(
        label="Notes (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=200,
    )

    def __init__(self, item_id: int, cog):
        super().__init__()
        self.item_id = item_id
        self.cog     = cog

    async def on_submit(self, interaction: discord.Interaction):
        from app.quartermaster.models import StockUnit
        from app.discordauth.models import DiscordUser
        import decimal

        raw = self.scu_input.value.replace(",", "").strip()
        try:
            scu = decimal.Decimal(raw)
            if scu <= 0:
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ SCU volume must be a positive number (e.g. 818.0).", ephemeral=True)
            return

        metadata = {}
        if self.ore_quality.value:
            metadata["ore_quality"] = self.ore_quality.value.strip()
        if self.source_location.value:
            metadata["source_location"] = self.source_location.value.strip()

        def _create():
            du, _ = DiscordUser.ensure_with_user(
                discorduid=interaction.user.id,
                access_token=None, refresh_token=None, access_token_expires=None,
            )
            unit = StockUnit.objects.create(
                item_id=self.item_id,
                scu_volume=scu,
                sc_metadata=metadata,
                condition_notes=self.condition_notes.value or "",
                added_by=du.user,
            )
            return unit, unit.item.name

        unit, item_name = await sync_to_async(_create)()
        embed = discord.Embed(
            title="✅ Bulk Stock Added",
            description=f"**{scu} SCU** of **{item_name}** logged (Unit #{unit.pk})",
            color=0x22C55E,
        )
        if metadata.get("ore_quality"):
            embed.add_field(name="Quality", value=metadata["ore_quality"], inline=True)
        if metadata.get("source_location"):
            embed.add_field(name="Source", value=metadata["source_location"], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ScanFailedView(discord.ui.View):
    """Shown when image scan fails — lets user open the manual modal instead."""

    def __init__(self, item_id: int, mode: str, craft_state: str = None, cog=None):
        super().__init__(timeout=180)
        self.item_id     = item_id
        self.mode        = mode
        self.craft_state = craft_state
        self.cog         = cog

    @discord.ui.button(label="✏️ Enter Manually", style=discord.ButtonStyle.primary)
    async def manual_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        if self.mode == "BULK_SCU":
            await interaction.response.send_modal(BulkSCUModal(self.item_id, self.cog))
        else:
            await interaction.response.send_modal(UnitStatsModal(self.item_id, self.cog, craft_state=self.craft_state))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


class ConfirmBulkScanView(discord.ui.View):
    def __init__(self, item_id: int, quantities: list[float], unit_label: str, ore_quality: str, cog):
        super().__init__(timeout=300)
        self.item_id    = item_id
        self.quantities = quantities
        self.unit_label = unit_label
        self.ore_quality = ore_quality
        self.cog        = cog

    @discord.ui.button(label="✅ Save All Units", style=discord.ButtonStyle.success)
    async def save_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        from app.quartermaster.models import StockUnit
        from app.discordauth.models import DiscordUser
        import decimal

        quantities   = self.quantities
        unit_label   = self.unit_label
        ore_quality  = self.ore_quality
        is_scu       = unit_label == "SCU"

        def _bulk_create():
            du, _ = DiscordUser.ensure_with_user(
                discorduid=interaction.user.id,
                access_token=None, refresh_token=None, access_token_expires=None,
            )
            units = StockUnit.objects.bulk_create([
                StockUnit(
                    item_id=self.item_id,
                    scu_volume=decimal.Decimal(str(q)) if is_scu else None,
                    sc_metadata={
                        **({"quantity": q, "unit": unit_label} if not is_scu else {}),
                        **({"ore_quality": ore_quality} if ore_quality else {}),
                    },
                    added_by=du.user,
                )
                for q in quantities
            ])
            return units

        units = await sync_to_async(_bulk_create)()
        total = sum(quantities)
        fmt   = ".3f" if is_scu else ".1f"
        embed = discord.Embed(
            title="✅ Bulk Stock Saved",
            description=f"Created **{len(units)}** units totalling **{total:{fmt}} {unit_label}**.",
            color=0x22C55E,
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


class ConfirmScanView(discord.ui.View):
    def __init__(self, item_id: int, item_name: str, stats: dict, cog, craft_state: str = None):
        super().__init__(timeout=300)
        self.item_id     = item_id
        self.item_name   = item_name
        self.stats       = stats
        self.cog         = cog
        self.craft_state = craft_state

    @discord.ui.button(label="✅ Save as-is", style=discord.ButtonStyle.success)
    async def save_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        from app.quartermaster.models import StockUnit
        from app.discordauth.models import DiscordUser

        craft_state = self.craft_state

        def _create():
            du, _ = DiscordUser.ensure_with_user(
                discorduid=interaction.user.id,
                access_token=None, refresh_token=None, access_token_expires=None,
            )
            return StockUnit.objects.create(item_id=self.item_id, stats=self.stats, craft_state=craft_state, added_by=du.user)

        unit = await sync_to_async(_create)()
        stats_lines = "\n".join(f"• **{k}**: {v}" for k, v in self.stats.items()) or "*No stats*"
        embed = discord.Embed(title="✅ Unit Added to Stock", description=f"**Unit #{unit.pk}** added to **{self.item_name}**", color=0x22C55E)
        embed.add_field(name="Stats", value=stats_lines, inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="✏️ Edit Stats", style=discord.ButtonStyle.secondary)
    async def edit_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.send_modal(UnitStatsModal(self.item_id, self.cog, prefill_stats=self.stats, craft_state=self.craft_state))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


# ---------------------------------------------------------------------------
# Weapon tracker modals/views
# ---------------------------------------------------------------------------

class WeaponDataModal(discord.ui.Modal, title="Verify Weapon Data"):
    weapon_name = discord.ui.TextInput(label="Weapon Name", style=discord.TextStyle.short, required=True)
    weapon_stats = discord.ui.TextInput(
        label="Crafted Stats (JSON format)",
        style=discord.TextStyle.paragraph,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            stats_dict = json.loads(self.weapon_stats.value) if self.weapon_stats.value.strip() else {}
        except json.JSONDecodeError:
            await interaction.response.send_message("❌ Stats must be valid JSON.", ephemeral=True)
            return

        def inner():
            from app.quartermaster.models import TrackedCraftedWeapon, TrackedCraftedWeaponState
            from app.discordauth.models import DiscordUser
            with transaction.atomic():
                du, _ = DiscordUser.ensure_with_user(
                    discorduid=interaction.user.id,
                    access_token=None, refresh_token=None, access_token_expires=None,
                )
                item = TrackedCraftedWeapon(submitter=du.user, weapon_name=self.weapon_name.value, stats=stats_dict)
                item._history_user = du.user
                item.save()
                state = TrackedCraftedWeaponState(for_weapon=item, status=TrackedCraftedWeaponState.Status.HELD_EXTERNALLY)
                state._history_user = du.user
                state.save()

        await sync_to_async(inner)()
        await interaction.response.send_message(f"✅ Saved **{self.weapon_name.value}**!", ephemeral=True)
        await self.cog.refresh_inventory_listing(interaction.guild)


class WeaponRequestModal(discord.ui.Modal, title="Request Weapon"):
    note = discord.ui.TextInput(
        label="Note/Purpose", style=discord.TextStyle.paragraph,
        required=False, placeholder="Why do you need this weapon?",
    )

    def __init__(self, weapon_id: int, cog):
        super().__init__()
        self.weapon_id = weapon_id
        self.cog       = cog

    async def on_submit(self, interaction: discord.Interaction):
        from app.quartermaster.models import TrackedCraftedWeapon, TrackedCraftedRequests
        from app.discordauth.models import DiscordUser

        try:
            discord_user = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
        except DiscordUser.DoesNotExist:
            await interaction.response.send_message("❌ Account not linked.", ephemeral=True)
            return

        weapon = await TrackedCraftedWeapon.objects.select_related("state").aget(id=self.weapon_id)
        if await TrackedCraftedRequests.objects.filter(submitter=discord_user.user, for_weapon=weapon, accepted=None).aexists():
            await interaction.response.send_message("❌ You already have a pending request for this weapon.", ephemeral=True)
            return

        await TrackedCraftedRequests.objects.acreate(submitter=discord_user.user, for_weapon=weapon, note=self.note.value)
        await interaction.response.send_message("✅ Request submitted!", ephemeral=True)
        await self.cog.refresh_inventory_listing(interaction.guild)


class WeaponStatusSelect(discord.ui.Select):
    def __init__(self, weapon_id: int, cog):
        from app.quartermaster.models import TrackedCraftedWeaponState
        self.weapon_id = weapon_id
        self.cog       = cog
        options = [
            discord.SelectOption(label="Held (Knights)",   value=TrackedCraftedWeaponState.Status.HELD_AVAILIBLE_LIMITED),
            discord.SelectOption(label="Held (Org-wide)",  value=TrackedCraftedWeaponState.Status.HELD_AVAILIBLE_ORGWIDE),
            discord.SelectOption(label="Held Externally",  value=TrackedCraftedWeaponState.Status.HELD_EXTERNALLY),
            discord.SelectOption(label="Awaiting Return",  value=TrackedCraftedWeaponState.Status.AWAITING_RETURN),
        ]
        super().__init__(placeholder="Update weapon status...", options=options)

    async def callback(self, interaction: discord.Interaction):
        from app.quartermaster.models import TrackedCraftedWeapon
        from app.discordauth.models import DiscordUser
        try:
            discord_user = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
        except DiscordUser.DoesNotExist:
            await interaction.response.send_message("❌ Account not linked.", ephemeral=True)
            return

        weapon = await TrackedCraftedWeapon.objects.select_related("state").aget(id=self.weapon_id)
        state = weapon.state
        state.status       = self.values[0]
        state.current_owner = None
        state.set_editing_user(discord_user.user)
        await state.asave()
        await interaction.response.send_message(f"✅ Status updated to **{state.get_status_display()}**", ephemeral=True)
        await self.cog.refresh_inventory_listing(interaction.guild)


class WeaponStatusView(discord.ui.View):
    def __init__(self, weapon_id: int, cog):
        super().__init__(timeout=300)
        self.add_item(WeaponStatusSelect(weapon_id, cog))


class WeaponRequestSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Select a request to APPROVE...", options=options)

    async def callback(self, interaction: discord.Interaction):
        from app.quartermaster.models import TrackedCraftedRequests, TrackedCraftedWeaponState
        view: "WeaponRequestReviewView" = self.view
        request_id = int(self.values[0])

        def inner():
            with transaction.atomic():
                target_req = TrackedCraftedRequests.objects.select_related("submitter", "for_weapon__state").get(id=request_id)
                target_req.accepted  = True
                target_req.closed_at = timezone.now()
                target_req.save()
                TrackedCraftedRequests.objects.filter(for_weapon=target_req.for_weapon, accepted=None).update(
                    accepted=False, closed_at=timezone.now()
                )
                state = target_req.for_weapon.state
                state.status        = TrackedCraftedWeaponState.Status.IN_USE
                state.current_owner = target_req.submitter
                state.purpose       = f"Approved request: {target_req.note}"
                state.save()
                return target_req

        target_req = await sync_to_async(inner)()
        name = await target_req.submitter.aget_display_name()
        await interaction.response.send_message(f"✅ Approved {name}. Weapon is now **IN USE**.", ephemeral=True)
        await view.cog.refresh_inventory_listing(interaction.guild)


class WeaponRequestReviewView(discord.ui.View):
    def __init__(self, weapon_id: int, cog, options: list):
        super().__init__(timeout=300)
        self.weapon_id = weapon_id
        self.cog       = cog
        self.add_item(WeaponRequestSelect(options))


class WeaponInventoryView(discord.ui.View):
    def __init__(self, weapon_id: int, cog):
        super().__init__(timeout=None)
        self.weapon_id = weapon_id
        self.cog       = cog

    @discord.ui.button(label="Request Weapon", style=discord.ButtonStyle.primary, custom_id="weapon_request_btn")
    async def request_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from app.quartermaster.models import TrackedCraftedWeapon, TrackedCraftedWeaponState
        weapon = await TrackedCraftedWeapon.objects.select_related("state").aget(id=self.weapon_id)
        avail_statuses = [TrackedCraftedWeaponState.Status.HELD_AVAILIBLE_LIMITED, TrackedCraftedWeaponState.Status.HELD_AVAILIBLE_ORGWIDE]
        if weapon.state.status not in avail_statuses:
            await interaction.response.send_message("❌ This weapon is not currently available for request.", ephemeral=True)
            return
        perm = (
            "loot_tracker.can_request_held_limited"
            if weapon.state.status == TrackedCraftedWeaponState.Status.HELD_AVAILIBLE_LIMITED
            else "loot_tracker.can_request_held_orgwide"
        )
        try:
            await _verify_and_cache_generic_permission(interaction.user.id, perm)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_modal(WeaponRequestModal(self.weapon_id, self.cog))

    @discord.ui.button(label="Manage Requests", style=discord.ButtonStyle.secondary, custom_id="weapon_manage_btn")
    async def manage_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from app.quartermaster.models import TrackedCraftedRequests
        try:
            await _verify_and_cache_generic_permission(interaction.user.id, "loot_tracker.can_approve_requests")
        except Exception:
            await interaction.response.send_message("🛡️ Staff only.", ephemeral=True)
            return

        embed   = discord.Embed(title="Pending Requests", color=discord.Color.orange())
        options = []
        async for req in TrackedCraftedRequests.objects.filter(for_weapon_id=self.weapon_id, accepted=None).select_related("submitter"):
            name = await req.submitter.aget_display_name()
            embed.add_field(name=f"From {name}", value=f"Note: {req.note or 'N/A'}", inline=False)
            options.append(discord.SelectOption(label=f"Approve {name}", value=str(req.id)))

        if not options:
            await interaction.response.send_message("No pending requests.", ephemeral=True)
            return

        await interaction.response.send_message(embed=embed, view=WeaponRequestReviewView(self.weapon_id, self.cog, options), ephemeral=True)

    @discord.ui.button(label="Update Status", style=discord.ButtonStyle.secondary, custom_id="weapon_status_btn")
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await _verify_and_cache_generic_permission(interaction.user.id, "loot_tracker.can_discord_manage_state")
        except Exception:
            await interaction.response.send_message("🛡️ You do not have permission to manage weapon states.", ephemeral=True)
            return
        await interaction.response.send_message("Select a new status:", view=WeaponStatusView(self.weapon_id, self.cog), ephemeral=True)

    @discord.ui.button(label="Return Weapon", style=discord.ButtonStyle.danger, custom_id="weapon_return_btn")
    async def return_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from app.quartermaster.models import TrackedCraftedWeapon, TrackedCraftedWeaponState
        from app.discordauth.models import DiscordUser
        weapon = await TrackedCraftedWeapon.objects.select_related("state__current_owner").aget(id=self.weapon_id)
        if not weapon.state.current_owner:
            await interaction.response.send_message("❌ This weapon is not currently checked out.", ephemeral=True)
            return
        try:
            discord_user = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
            if weapon.state.current_owner != discord_user.user:
                await interaction.response.send_message("❌ Only the current owner can return this weapon.", ephemeral=True)
                return
        except DiscordUser.DoesNotExist:
            await interaction.response.send_message("❌ Account not linked.", ephemeral=True)
            return

        state = weapon.state
        state.status  = TrackedCraftedWeaponState.Status.AWAITING_RETURN
        state.purpose = f"Returned by owner via Discord at {timezone.now()}"
        state.set_editing_user(discord_user.user)
        await state.asave()
        await interaction.response.send_message("✅ Weapon marked as **Awaiting Return**.", ephemeral=True)
        await self.cog.refresh_inventory_listing(interaction.guild)


class ReviewDataView(discord.ui.View):
    def __init__(self, weapon_name: str, stats_dict: dict, cog):
        super().__init__(timeout=300)
        self.weapon_name = weapon_name
        self.stats_dict  = stats_dict
        self.cog         = cog

    @discord.ui.button(label="Review & Save Data", style=discord.ButtonStyle.success, emoji="💾")
    async def review_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WeaponDataModal()
        modal.cog = self.cog
        modal.weapon_name.default  = self.weapon_name
        modal.weapon_stats.default = json.dumps(self.stats_dict, indent=2)
        await interaction.response.send_modal(modal)


# ---------------------------------------------------------------------------
# Merits modal
# ---------------------------------------------------------------------------

class _PrisonTimeModal(discord.ui.Modal, title="Prison Time → Merits"):
    prison_time = discord.ui.TextInput(
        label="Prison Time",
        placeholder="e.g. 1h 30m, 45m, 2h",
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        from app.quartermaster.prison_time import parse_prison_time, format_prison_time
        from app.quartermaster.models import MeritRequest
        from app.discordauth.models import DiscordUser

        seconds = parse_prison_time(self.prison_time.value)
        if seconds is None:
            await interaction.response.send_message(
                "❌ Couldn't parse that time. Use formats like `1h 30m`, `45m`, or `2h`.",
                ephemeral=True,
            )
            return

        try:
            duser = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
        except DiscordUser.DoesNotExist:
            await interaction.response.send_message("❌ No linked account. Log in to the website first.", ephemeral=True)
            return

        readable = format_prison_time(seconds)
        req = await MeritRequest.objects.acreate(
            requester=duser.user,
            kind=MeritRequest.Kind.MERIT,
            title="Prison Time Compensation",
            reason=f"SC prison sentence: {readable}",
            amount=seconds,
        )
        await interaction.response.send_message(
            f"✅ Submitted merit request **#{req.pk}** for **{seconds:,} pts** ({readable} of prison time).",
            ephemeral=True,
        )


class _MeritSubmitModal(discord.ui.Modal):
    """Kind-aware merit/aUEC submission modal — opened after choosing type in /qm merit."""
    title_field = discord.ui.TextInput(label="What is this for?", max_length=120)
    amount      = discord.ui.TextInput(label="Amount (merit pts or aUEC)", placeholder="e.g. 500", max_length=12)
    reason      = discord.ui.TextInput(label="Reason / Details", style=discord.TextStyle.paragraph, required=False, max_length=500)

    def __init__(self, kind: str):
        label = "Merit Award" if kind == "MERIT" else "aUEC Payout"
        super().__init__(title=f"Submit {label} Request")
        self.kind = kind

    async def on_submit(self, interaction: discord.Interaction):
        from app.quartermaster.models import MeritRequest
        from app.discordauth.models import DiscordUser
        try:
            amt = int(self.amount.value.replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("❌ Amount must be a whole number.", ephemeral=True)
            return
        try:
            duser = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
        except DiscordUser.DoesNotExist:
            await interaction.response.send_message("❌ No linked account. Log in to the website first.", ephemeral=True)
            return
        req = await MeritRequest.objects.acreate(
            requester=duser.user, kind=self.kind,
            title=self.title_field.value.strip(),
            reason=self.reason.value.strip(), amount=amt,
        )
        kind_label = "aUEC" if self.kind == "MONEY" else "merit pts"
        await interaction.response.send_message(
            f"✅ **{req.title}** submitted — **{amt:,} {kind_label}** (#{req.pk}). Staff will review it shortly.",
            ephemeral=True,
        )


async def _process_merit_request(interaction: discord.Interaction, request_id: int, action: str, note: str):
    from app.quartermaster.models import MeritRequest
    from app.discordauth.models import DiscordUser
    req = await MeritRequest.objects.select_related("requester").filter(
        pk=request_id, status=MeritRequest.Status.PENDING,
    ).afirst()
    if not req:
        await interaction.response.send_message("❌ Request not found or already processed.", ephemeral=True)
        return
    try:
        duser = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
        req.reviewed_by = duser.user
    except DiscordUser.DoesNotExist:
        pass
    now = timezone.now()
    req.status        = MeritRequest.Status.FULFILLED if action == "FULFILL" else MeritRequest.Status.DENIED
    req.reviewer_note = note.strip()
    req.reviewed_at   = now
    if action == "FULFILL":
        req.fulfilled_at = now
    await req.asave(update_fields=["status", "reviewer_note", "reviewed_at", "fulfilled_at", "reviewed_by_id"])
    kind_label = "aUEC" if req.kind == MeritRequest.Kind.MONEY else "merit pts"
    emoji      = "✅" if action == "FULFILL" else "❌"
    await interaction.response.send_message(
        f"{emoji} **#{req.pk}** ({req.amount:,} {kind_label}) marked **{req.get_status_display()}**.",
        ephemeral=True,
    )


class _MeritFulfillModal(discord.ui.Modal, title="Fulfill Request"):
    note = discord.ui.TextInput(label="Note for the member (optional)", required=False, max_length=300, style=discord.TextStyle.paragraph)

    def __init__(self, request_id: int):
        super().__init__()
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction):
        await _process_merit_request(interaction, self.request_id, "FULFILL", self.note.value)


class _MeritDenyModal(discord.ui.Modal, title="Deny Request"):
    note = discord.ui.TextInput(label="Reason for denial (optional)", required=False, max_length=300, style=discord.TextStyle.paragraph)

    def __init__(self, request_id: int):
        super().__init__()
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction):
        await _process_merit_request(interaction, self.request_id, "DENY", self.note.value)


class _MeritReviewView(discord.ui.View):
    def __init__(self, requests):
        super().__init__(timeout=300)
        for req in requests[:5]:
            kind_label = "aUEC" if req.kind == "MONEY" else "pts"
            short = f"#{req.pk} · {req.amount:,} {kind_label}"
            self.add_item(_MeritFulfillButton(req.pk, short))
            self.add_item(_MeritDenyButton(req.pk, short))


class _MeritFulfillButton(discord.ui.Button):
    def __init__(self, request_id: int, label: str):
        super().__init__(label=f"✅ Fulfill {label}"[:80], style=discord.ButtonStyle.success)
        self.request_id = request_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_MeritFulfillModal(self.request_id))


class _MeritDenyButton(discord.ui.Button):
    def __init__(self, request_id: int, label: str):
        super().__init__(label=f"❌ Deny {label}"[:80], style=discord.ButtonStyle.danger)
        self.request_id = request_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_MeritDenyModal(self.request_id))


# ---------------------------------------------------------------------------
# Unit management views/modals  (used by /qm manage)
# ---------------------------------------------------------------------------

class _AssignUnitModal(discord.ui.Modal, title="Assign Unit to Member"):
    member_name = discord.ui.TextInput(label="Member display name or RSI handle", max_length=150)
    notes       = discord.ui.TextInput(label="Notes (optional)", required=False, max_length=300, style=discord.TextStyle.short)

    def __init__(self, unit_uid: str, item_name: str, cog):
        super().__init__()
        self.unit_uid  = unit_uid
        self.item_name = item_name
        self.cog       = cog

    async def on_submit(self, interaction: discord.Interaction):
        from app.quartermaster.models import StockUnit, StockAssignment
        from app.unifieduser.models import OrgPlayer
        from app.discordauth.models import DiscordUser
        from django.db.models import Q as DQ
        await interaction.response.defer(ephemeral=True)

        unit = await StockUnit.objects.select_related("item").filter(uid=self.unit_uid).afirst()
        if not unit or unit.status != StockUnit.Status.AVAILABLE:
            await interaction.followup.send("❌ Unit is no longer available.", ephemeral=True)
            return

        name = self.member_name.value.strip()
        recipient = await OrgPlayer.objects.filter(
            DQ(displaynamesearchcache__display_name__iexact=name) | DQ(username__iexact=name)
        ).afirst()

        def _do_assign():
            du, _ = DiscordUser.ensure_with_user(discorduid=interaction.user.id, access_token=None, refresh_token=None, access_token_expires=None)
            with transaction.atomic():
                unit.status         = StockUnit.Status.ASSIGNED
                unit.current_holder = recipient
                unit.save(update_fields=["status", "current_holder"])
                StockAssignment.objects.create(
                    unit=unit, action=StockAssignment.Action.ASSIGNED,
                    recipient=recipient, recipient_name=name if not recipient else "",
                    notes=self.notes.value or "", actioned_by=du.user,
                )

        await sync_to_async(_do_assign)()
        stats_lines = "\n".join(f"• **{k}**: {v}" for k, v in unit.stats.items()) or "*No stats*"
        embed = discord.Embed(title=f"📦 Assigned — {unit.item.name}", color=0x5865F2)
        embed.add_field(name="Member", value=name, inline=True)
        embed.add_field(name="Unit",   value=f"#{unit.pk}", inline=True)
        embed.add_field(name="Stats",  value=stats_lines, inline=False)
        if self.notes.value:
            embed.add_field(name="Notes", value=self.notes.value, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


class _ReturnLostModal(discord.ui.Modal):
    notes = discord.ui.TextInput(label="Notes (optional)", required=False, max_length=300, style=discord.TextStyle.short)

    def __init__(self, unit_uid: str, item_name: str, action: str, cog):
        super().__init__(title="Mark as Returned" if action == "return" else "Mark as Lost")
        self.unit_uid  = unit_uid
        self.item_name = item_name
        self.action    = action  # "return" or "lost"
        self.cog       = cog

    async def on_submit(self, interaction: discord.Interaction):
        from app.quartermaster.models import StockUnit, StockAssignment
        from app.discordauth.models import DiscordUser
        await interaction.response.defer(ephemeral=True)

        unit = await StockUnit.objects.select_related("item", "current_holder").filter(uid=self.unit_uid).afirst()
        if not unit:
            await interaction.followup.send("❌ Unit not found.", ephemeral=True)
            return

        prev_holder = unit.current_holder

        def _do():
            du, _ = DiscordUser.ensure_with_user(discorduid=interaction.user.id, access_token=None, refresh_token=None, access_token_expires=None)
            with transaction.atomic():
                unit.status         = StockUnit.Status.AVAILABLE if self.action == "return" else StockUnit.Status.LOST
                unit.current_holder = None
                unit.save(update_fields=["status", "current_holder"])
                log_action = StockAssignment.Action.RETURNED if self.action == "return" else StockAssignment.Action.LOST
                StockAssignment.objects.create(
                    unit=unit, action=log_action,
                    recipient=prev_holder,
                    recipient_name=prev_holder.display_name if prev_holder else "",
                    notes=self.notes.value or "", actioned_by=du.user,
                )

        await sync_to_async(_do)()
        if self.action == "return":
            embed = discord.Embed(title=f"🔄 Returned — {unit.item.name}", color=0x22C55E)
            if prev_holder:
                embed.add_field(name="Returned from", value=prev_holder.display_name, inline=True)
        else:
            embed = discord.Embed(title=f"💀 Lost — {unit.item.name}", color=0xEF4444)
            if prev_holder:
                embed.add_field(name="Last held by", value=prev_holder.display_name, inline=True)
        if self.notes.value:
            embed.add_field(name="Notes", value=self.notes.value, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


class _RetireUnitModal(discord.ui.Modal, title="Retire Unit"):
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, max_length=300, style=discord.TextStyle.short)

    def __init__(self, unit_uid: str, item_name: str, cog):
        super().__init__()
        self.unit_uid  = unit_uid
        self.item_name = item_name
        self.cog       = cog

    async def on_submit(self, interaction: discord.Interaction):
        from app.quartermaster.models import StockUnit
        await interaction.response.defer(ephemeral=True)
        unit = await StockUnit.objects.select_related("item").filter(uid=self.unit_uid).afirst()
        if not unit:
            await interaction.followup.send("❌ Unit not found.", ephemeral=True)
            return
        unit.status          = StockUnit.Status.RETIRED
        unit.condition_notes = (unit.condition_notes + f"\n[Retired] {self.reason.value}").strip() if self.reason.value else unit.condition_notes
        await unit.asave(update_fields=["status", "condition_notes", "updated_at"])
        embed = discord.Embed(title=f"🗑️ Retired — {unit.item.name}", description=f"Unit #{unit.pk}", color=0x6B7280)
        if self.reason.value:
            embed.add_field(name="Reason", value=self.reason.value, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


class _UnitActionView(discord.ui.View):
    """Action buttons shown after a unit is selected in /qm manage."""

    def __init__(self, unit_uid: str, item_name: str, unit_status: str, cog):
        super().__init__(timeout=300)
        self.unit_uid    = unit_uid
        self.item_name   = item_name
        self.unit_status = unit_status
        self.cog         = cog

    @discord.ui.button(label="Assign to Member", style=discord.ButtonStyle.primary, emoji="📦")
    async def assign_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if self.unit_status != "AVAILABLE":
            await interaction.response.send_message("❌ Only AVAILABLE units can be assigned.", ephemeral=True)
            return
        await interaction.response.send_modal(_AssignUnitModal(self.unit_uid, self.item_name, self.cog))

    @discord.ui.button(label="Mark Returned", style=discord.ButtonStyle.success, emoji="🔄")
    async def return_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if self.unit_status != "ASSIGNED":
            await interaction.response.send_message("❌ Only ASSIGNED units can be returned.", ephemeral=True)
            return
        await interaction.response.send_modal(_ReturnLostModal(self.unit_uid, self.item_name, "return", self.cog))

    @discord.ui.button(label="Mark Lost", style=discord.ButtonStyle.danger, emoji="💀")
    async def lost_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(_ReturnLostModal(self.unit_uid, self.item_name, "lost", self.cog))

    @discord.ui.button(label="Retire Unit", style=discord.ButtonStyle.secondary, emoji="🗑️")
    async def retire_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(_RetireUnitModal(self.unit_uid, self.item_name, self.cog))


class _UnitSelectView(discord.ui.View):
    """Select menu of all units for an item — leads into _UnitActionView."""

    def __init__(self, units: list[dict], item_name: str, cog):
        super().__init__(timeout=120)
        self.cog            = cog
        self.units_by_uid   = {u["uid"]: u for u in units}

        options = []
        for u in units[:25]:
            desc = f"Holder: {u['holder']}  ·  {u['stats_hint']}"
            options.append(discord.SelectOption(
                label=f"#{u['pk']} · {u['status_display']}"[:100],
                description=desc[:100],
                value=u["uid"],
                emoji=_STATUS_EMOJI.get(u["status"], "⚪"),
            ))

        sel = discord.ui.Select(placeholder="Select a unit to manage...", options=options)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        uid    = interaction.data["values"][0]
        unit_d = self.units_by_uid.get(uid, {})
        status = unit_d.get("status", "AVAILABLE")
        item_name = unit_d.get("item_name", "Item")
        pk     = unit_d.get("pk", "?")
        await interaction.response.edit_message(
            content=f"**Unit #{pk}** — {_STATUS_EMOJI.get(status, '⚪')} {unit_d.get('status_display', status)}  ·  Holder: **{unit_d.get('holder', '—')}**\nChoose an action:",
            view=_UnitActionView(uid, item_name, status, self.cog),
        )


# ---------------------------------------------------------------------------
# Unified Cog
# ---------------------------------------------------------------------------

class LootCog(commands.Cog, name="Loot Tracker"):

    def __init__(self, client: discord.Client):
        self.client      = client
        self._redis      = None
        self._pubsub     = None
        self._redis_task = None
        self._inv_channel = None
        self._groq_client = None

    async def cog_load(self):
        import redis.asyncio as _redis

        redis_url          = settings.CELERY_BROKER_URL
        self._redis        = _redis.from_url(redis_url, decode_responses=True)
        self._pubsub       = self._redis.pubsub()
        await self._pubsub.subscribe(PUBSUB_CHANNEL)
        self._redis_task   = self.client.loop.create_task(self._listen())
        from groq import AsyncGroq
        self._groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

        # Register persistent views so buttons survive restarts
        self.client.add_view(InventoryPanelView(cog=self))
        self.client.add_view(WeaponInventoryView(0, self))
        self.client.add_view(EquipmentPanelView(cog=self))
        self.client.add_view(EquipmentRequestActionView(0, cog=self))
        log.info("[LOOT COG] loaded.")

    async def _vision_scan(self, img_bytes: bytes, media_type: str, prompt: str) -> str:
        import base64
        img_b64 = base64.standard_b64encode(img_bytes).decode()
        resp = await self._groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            temperature=0.0,
            max_tokens=1024,
        )
        return resp.choices[0].message.content

    async def cog_unload(self):
        if self._redis_task:
            self._redis_task.cancel()
        try:
            await self._pubsub.unsubscribe()
            await self._redis.aclose()
        except Exception:
            pass

    # ── Redis listener ────────────────────────────────────────────────────────

    async def _listen(self):
        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data   = json.loads(message["data"])
                    action = data.get("action")

                    if action == "STOCK_UPDATED":
                        await self._refresh_embed()
                        await self._refresh_equipment_embed()

                    elif action in ("CraftedWeapon.updated", "CraftedWeaponState.updated", "CraftedRequest.updated"):
                        guild_id = int(await aget_global_preference(BotGuildID.import_path) or 0)
                        guild    = self.client.get_guild(guild_id)
                        if guild:
                            await self.refresh_inventory_listing(guild)

                    elif action == "StockAssignment.new":
                        await self._post_assignment_log(data)

                    elif action == "MERIT_REQUEST_SUBMITTED":
                        await self._merit_on_submitted(data)

                    elif action == "MERIT_REQUEST_FULFILLED":
                        await self._merit_dm_requester(data, "fulfilled 🎉", discord.Color.green())
                        await self._merit_on_processed(data, fulfilled=True)

                    elif action == "MERIT_REQUEST_DENIED":
                        await self._merit_dm_requester(data, "denied ❌", discord.Color.red())
                        await self._merit_on_processed(data, fulfilled=False)

                except Exception:
                    log.exception("[LOOT COG] listener event error")
        except Exception as exc:
            log.error("[LOOT COG] listener crashed: %s — restarting in 15s", exc)
            import asyncio
            await asyncio.sleep(15)
            self._redis_task = self.client.loop.create_task(self._listen())

    # ── Live loot inventory embed ─────────────────────────────────────────────

    async def _get_inv_channel(self):
        if self._inv_channel:
            return self._inv_channel
        chan_id = int(await aget_global_preference(LootInventoryChannelID.import_path) or 0)
        if not chan_id:
            return None
        self._inv_channel = self.client.get_channel(chan_id)
        return self._inv_channel

    async def _refresh_embed(self):
        import asyncio
        channel = await self._get_inv_channel()
        if not channel:
            return
        items = await _get_active_items()
        embed = _build_stockpile_embed(items, self.client.user)
        view  = InventoryPanelView(cog=self)

        from app.quartermaster.models import LootStatusMessage
        rec = await LootStatusMessage.get_singleton()

        if rec.message_id:
            for attempt in range(3):
                try:
                    msg = await channel.fetch_message(rec.message_id)
                    await msg.edit(embed=embed, view=view)
                    return
                except discord.NotFound:
                    break
                except discord.DiscordServerError:
                    if attempt < 2:
                        await asyncio.sleep(5 * (attempt + 1))
                    else:
                        log.warning("[LOOT COG] 503 after 3 attempts, skipping embed update.")
                        return

        new_msg = await channel.send(embed=embed, view=view)

        @sync_to_async
        def _save(mid=new_msg.id):
            from app.quartermaster.models import LootStatusMessage
            LootStatusMessage.objects.update_or_create(id=1, defaults={"message_id": mid})

        await _save()

    # ── Request thread ────────────────────────────────────────────────────────

    async def _open_request_thread(self, guild: discord.Guild, req):
        chan_id = int(await aget_global_preference(LootRequestChannelID.import_path) or 0)
        if not chan_id:
            log.warning("[LOOT COG] LootRequestChannelID not configured.")
            return
        channel = guild.get_channel(chan_id)
        if not channel:
            return

        direction_label = "Input" if req.direction == "INPUT" else "Withdraw"
        thread = await channel.create_thread(
            name=f"[{direction_label}] {req.item.name} ×{req.quantity:,} — #{req.pk}"[:100],
            type=discord.ChannelType.private_thread,
            reason=f"Loot request #{req.pk}",
        )
        msg = await thread.send(embed=_build_request_embed(req), view=RequestActionView(request_id=req.pk))
        await _save_thread_ids(req.pk, thread.id, msg.id)

        try:
            discord_id = await sync_to_async(lambda: req.requester.discorduser.discorduid)()
            member = guild.get_member(discord_id) or await guild.fetch_member(discord_id)
            await thread.add_user(member)
        except Exception:
            pass

        group_str = await aget_global_preference(LootQMGroupIDs.import_path) or ""
        qm_ids    = await _get_qm_discord_ids(group_str)
        for qm_discord_id in qm_ids:
            try:
                qm = guild.get_member(qm_discord_id) or await guild.fetch_member(qm_discord_id)
                await thread.add_user(qm)
            except Exception:
                pass

        if qm_ids:
            mentions = " ".join(f"<@{i}>" for i in qm_ids)
            await thread.send(f"📬 New loot **{direction_label.lower()}** request requires QM approval.\n{mentions}", silent=False)

    # ── Equipment channel embed ───────────────────────────────────────────────

    async def _get_equip_channel(self):
        chan_id = int(await aget_global_preference(EquipmentChannelID.import_path) or 0)
        if not chan_id:
            return None
        return self.client.get_channel(chan_id)

    async def _refresh_equipment_embed(self):
        channel = await self._get_equip_channel()
        if not channel:
            return
        items = await _get_craftable_items()
        embed = _build_equipment_embed(items, self.client.user)
        view  = EquipmentPanelView(cog=self)

        async for msg in channel.history(limit=10, oldest_first=True):
            if (
                msg.author == self.client.user
                and msg.embeds
                and msg.embeds[0].title == "⚒️ BlightVeil Equipment Panel"
            ):
                await msg.edit(embed=embed, view=view)
                return

        await channel.send(embed=embed, view=view)

    async def _open_equipment_request_thread(self, guild: discord.Guild, req):
        chan_id = int(await aget_global_preference(EquipmentRequestChannelID.import_path) or 0)
        if not chan_id:
            log.warning("[LOOT COG] EquipmentRequestChannelID not configured.")
            return
        channel = guild.get_channel(chan_id)
        if not channel:
            return

        try:
            requester_name = req.requester.display_name
        except Exception:
            requester_name = str(req.requester)

        thread = await channel.create_thread(
            name=f"[Equip] {req.stock_item.name} — {requester_name} #{req.pk}"[:100],
            type=discord.ChannelType.private_thread,
            reason=f"Equipment request #{req.pk}",
        )

        embed = discord.Embed(
            title=f"⚒️ Equipment Request — {req.stock_item.name}",
            color=0x7C3AED,
            timestamp=timezone.now(),
        )
        try:
            embed.add_field(name="Requested by", value=f"<@{req.requester.discorduser.discorduid}>", inline=True)
        except Exception:
            embed.add_field(name="Requested by", value=requester_name, inline=True)
        embed.add_field(name="Item", value=req.stock_item.name, inline=True)
        if req.notes:
            embed.add_field(name="Notes", value=req.notes, inline=False)
        embed.set_footer(text=f"Request #{req.pk} · {_site_url()}loot/equipment/requests/")

        msg = await thread.send(embed=embed, view=EquipmentRequestActionView(req.pk, cog=self))
        await _save_equipment_thread_ids(req.pk, thread.id, msg.id)

        try:
            discord_id = await sync_to_async(lambda: req.requester.discorduser.discorduid)()
            member = guild.get_member(discord_id) or await guild.fetch_member(discord_id)
            await thread.add_user(member)
        except Exception:
            pass

        group_str = await aget_global_preference(LootQMGroupIDs.import_path) or ""
        qm_ids    = await _get_qm_discord_ids(group_str)
        for qm_discord_id in qm_ids:
            try:
                qm = guild.get_member(qm_discord_id) or await guild.fetch_member(qm_discord_id)
                await thread.add_user(qm)
            except Exception:
                pass

        if qm_ids:
            mentions = " ".join(f"<@{i}>" for i in qm_ids)
            await thread.send(
                f"⚒️ New equipment request for **{req.stock_item.name}** from <@{discord_id}> requires QM review.\n{mentions}",
                silent=False,
            )

    # ── Merit helpers ─────────────────────────────────────────────────────────

    async def _merit_staff_channel(self):
        chan_id = int(await aget_global_preference(MeritStaffChannelID.import_path) or 0)
        if not chan_id:
            return None
        ch = self.client.get_channel(chan_id)
        if not ch:
            try:
                ch = await self.client.fetch_channel(chan_id)
            except Exception:
                return None
        return ch

    async def _merit_on_submitted(self, data: dict):
        channel = await self._merit_staff_channel()
        if not channel:
            return
        emoji      = KIND_EMOJI.get(data.get("kind", ""), "📋")
        amount     = data.get("amount", 0)
        amount_str = f"{amount:,} aUEC" if data.get("kind") == "MONEY" else f"{amount} pts"
        embed = discord.Embed(
            title=f"{emoji} New {data.get('kind', '')} Request",
            description=data.get("title", ""),
            color=discord.Color.yellow(),
        )
        embed.add_field(name="Amount",     value=amount_str if amount else "TBD", inline=True)
        embed.add_field(name="Request ID", value=f"#{data.get('request_uid')}", inline=True)
        detail_url = data.get("detail_url", "")
        if detail_url:
            embed.add_field(name="Link", value=f"[Review & Fulfill]({detail_url})", inline=False)
        await channel.send(embed=embed)

    async def _merit_on_processed(self, data: dict, fulfilled: bool):
        channel = await self._merit_staff_channel()
        if not channel:
            return
        emoji      = KIND_EMOJI.get(data.get("kind", ""), "📋")
        amount     = data.get("amount", 0)
        amount_str = f"{amount:,} aUEC" if data.get("kind") == "MONEY" else f"{amount} pts"
        discord_id = data.get("discord_id")
        mention    = f"<@{discord_id}>" if discord_id else "Unknown"
        label  = "✅ Fulfilled" if fulfilled else "❌ Denied"
        color  = discord.Color.green() if fulfilled else discord.Color.red()
        embed  = discord.Embed(title=f"{emoji} Request {label}", description=f"**{data.get('title', '')}**", color=color)
        embed.add_field(name="Member", value=mention,                         inline=True)
        embed.add_field(name="Amount", value=amount_str if amount else "TBD", inline=True)
        if data.get("reviewer_note"):
            embed.add_field(name="Staff Note", value=data["reviewer_note"], inline=False)
        embed.set_footer(text=f"Request #{data.get('request_uid')}")
        await channel.send(embed=embed)

    async def _merit_dm_requester(self, data: dict, status_label: str, color: discord.Color):
        enabled = await aget_global_preference(MeritNotificationsEnabled.import_path)
        if not enabled:
            return
        discord_id = data.get("discord_id")
        if not discord_id:
            return
        try:
            user = await self.client.fetch_user(int(discord_id))
        except Exception:
            return

        emoji      = KIND_EMOJI.get(data.get("kind", ""), "📋")
        amount     = data.get("amount", 0)
        amount_str = f"{amount:,} aUEC" if data.get("kind") == "MONEY" else f"{amount} pts"
        embed = discord.Embed(title=f"{emoji} Request {status_label}", description=f"**{data.get('title', '')}**", color=color)
        if amount:
            embed.add_field(name="Amount", value=amount_str, inline=True)
        if data.get("reviewer_note"):
            embed.add_field(name="Staff Note", value=data["reviewer_note"], inline=False)
        embed.set_footer(text="BlightVeil Merit & Money System")
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            log.warning("Cannot DM merit notification to %s", discord_id)

    # ── Stock assignment log ──────────────────────────────────────────────────

    async def _post_assignment_log(self, data: dict):
        chan_id = await aget_global_preference(StockLogChannelID.import_path)
        if not chan_id:
            return
        channel = self.client.get_channel(int(chan_id))
        if channel is None:
            try:
                channel = await self.client.fetch_channel(int(chan_id))
            except (discord.NotFound, discord.Forbidden):
                return

        action    = data["log_action"]
        item_name = data["item_name"]
        unit_uid  = data["unit_uid"]
        recipient = data["recipient"]
        by        = data.get("actioned_by") or "Unknown"
        notes     = data.get("notes") or ""
        stats     = data.get("stats") or {}
        available = data.get("available_after", "?")
        low_stock = data.get("low_stock", False)
        threshold = data.get("low_stock_threshold", 2)

        action_colors = {"ASSIGNED": 0x5865F2, "RETURNED": 0x22C55E, "LOST": 0xEF4444}
        action_icons  = {"ASSIGNED": "📦",      "RETURNED": "🔄",      "LOST": "💀"}
        color  = action_colors.get(action, 0x888888)
        icon   = action_icons.get(action,  "📋")
        embed  = discord.Embed(title=f"{icon} Stock {action.title()} — {item_name}", color=color)
        embed.add_field(name="Unit",      value=unit_uid,      inline=True)
        embed.add_field(name="Member",    value=recipient,      inline=True)
        embed.add_field(name="By",        value=by,             inline=True)
        embed.add_field(name="Available", value=str(available), inline=True)
        if stats:
            embed.add_field(name="Stats", value="\n".join(f"• **{k}**: {v}" for k, v in stats.items()), inline=False)
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)
        if low_stock:
            embed.add_field(name="⚠️ Low Stock Warning", value=f"Only **{available}** unit(s) available (threshold: {threshold}). Restock needed.", inline=False)
            embed.color = 0xFFC107
        await channel.send(embed=embed)

    # ── Weapon inventory listing ──────────────────────────────────────────────

    async def refresh_inventory_listing(self, guild: discord.Guild):
        chan_id = await aget_global_preference(WeaponInventoryChannelID.import_path)
        if not chan_id:
            return
        channel = guild.get_channel(int(chan_id))
        if not channel:
            return

        from app.quartermaster.models import TrackedCraftedWeapon, TrackedCraftedWeaponState
        lost_statuses = [TrackedCraftedWeaponState.Status.LOST_CIG, TrackedCraftedWeaponState.Status.LOST_COMBAT]
        weapons_qs = TrackedCraftedWeapon.objects.exclude(
            state__status__in=lost_statuses
        ).select_related("state", "submitter", "state__current_owner").prefetch_related("requests")

        # Only manage messages that are weapon cards (identified by title prefix "📦 " + embed footer absence)
        existing = [
            msg async for msg in channel.history(limit=50, oldest_first=True)
            if msg.author == self.client.user
            and msg.embeds
            and msg.embeds[0].title
            and msg.embeds[0].title.startswith("📦 ")
            and not (msg.embeds[0].footer and "Loot Tracker" in (msg.embeds[0].footer.text or ""))
        ]
        weapon_list = [w async for w in weapons_qs]

        for i, weapon in enumerate(weapon_list):
            status = weapon.state.status
            color = discord.Color.green()
            if status == TrackedCraftedWeaponState.Status.IN_USE:
                color = discord.Color.blue()
            elif status == TrackedCraftedWeaponState.Status.AWAITING_RETURN:
                color = discord.Color.gold()

            embed = discord.Embed(
                title=f"📦 {weapon.weapon_name}",
                description=f"Status: **{weapon.state.get_status_display()}**",
                color=color,
            )
            stats_str = "\n".join(f"**{k}**: {v}" for k, v in weapon.stats.items())
            embed.add_field(name="Crafted Stats", value=stats_str or "None", inline=False)
            if weapon.state.current_owner:
                embed.add_field(name="Current Owner", value=await weapon.state.current_owner.aget_display_name(), inline=True)

            pending = []
            async for req in weapon.requests.filter(accepted=None).select_related("submitter"):
                pending.append(await req.submitter.aget_display_name())
            if pending:
                embed.add_field(name="Pending Requests", value=", ".join(pending), inline=False)

            view = WeaponInventoryView(weapon.id, self)
            if i < len(existing):
                await existing[i].edit(embed=embed, view=view)
            else:
                await channel.send(embed=embed, view=view)

        for extra in existing[len(weapon_list):]:
            try:
                await extra.delete()
            except discord.HTTPException:
                pass

    # ── Unified /qm command group ─────────────────────────────────────────────

    qm = app_commands.Group(name="qm", description="Quartermaster — loot stockpile & equipment")

    # ── /qm request — deposit or withdrawal (replaces /qm deposit + /qm withdraw) ──

    @qm.command(name="request", description="Submit a loot deposit or withdrawal request")
    @app_commands.describe(direction="Are you depositing items or withdrawing them?")
    @app_commands.choices(direction=[
        app_commands.Choice(name="📥 Deposit — I'm giving items to the org", value="INPUT"),
        app_commands.Choice(name="📤 Withdraw — I need items from the org",  value="WITHDRAW"),
    ])
    @requires_django_perm(_SUBMIT)
    async def qm_request(self, interaction: discord.Interaction, direction: str):
        items = await _get_active_items()
        if not items:
            await interaction.response.send_message("❌ No items configured yet.", ephemeral=True)
            return
        emoji = "📥" if direction == "INPUT" else "📤"
        label = "deposit" if direction == "INPUT" else "withdraw"
        await interaction.response.send_message(
            f"{emoji} Select the item you want to **{label}**:",
            view=_ItemSelect(items, direction, self),
            ephemeral=True,
        )

    # ── /qm status ───────────────────────────────────────────────────────────

    @qm.command(name="status", description="Show current loot stockpile (currencies, materials, etc.)")
    @requires_django_perm(_VIEW)
    async def qm_status(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            items = await _get_active_items()
            embed = _build_stockpile_embed(items, self.client.user)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            log.exception("[LOOT COG] /qm status error")
            if interaction.response.is_done():
                await interaction.followup.send("❌ Could not load stockpile.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Could not load stockpile.", ephemeral=True)

    # ── /qm set — QM directly sets a loot stock quantity ─────────────────────

    @qm.command(name="set", description="[QM] Set the current quantity of a stockpile item")
    @app_commands.describe(item="Stockpile item to update")
    @app_commands.autocomplete(item=_loot_item_autocomplete)
    @requires_django_perm(_MANAGE)
    async def qm_set(self, interaction: discord.Interaction, item: str):
        try:
            item_id = int(item)
        except ValueError:
            await interaction.response.send_message("❌ Select an item from the list.", ephemeral=True)
            return
        loot_item = await _get_item(item_id)
        if not loot_item:
            await interaction.response.send_message("❌ Item not found.", ephemeral=True)
            return

        try:
            current = loot_item.stock.quantity
        except Exception:
            current = 0

        class _SetQtyModal(discord.ui.Modal, title=f"Set: {loot_item.name}"):
            quantity = discord.ui.TextInput(
                label="New Quantity",
                default=str(current),
                placeholder="e.g. 1,500,000",
                max_length=20,
            )
            notes = discord.ui.TextInput(
                label="Notes (optional)",
                required=False,
                style=discord.TextStyle.short,
                max_length=200,
            )

            async def on_submit(inner_self, modal_interaction: discord.Interaction):
                raw = inner_self.quantity.value.replace(",", "").strip()
                try:
                    new_qty = int(raw)
                    if new_qty < 0:
                        raise ValueError
                except ValueError:
                    await modal_interaction.response.send_message("❌ Invalid quantity.", ephemeral=True)
                    return

                @sync_to_async
                def _save():
                    from app.quartermaster.models import LootStock
                    from app.unifieduser.models import DiscordUser as _DU
                    du = _DU.objects.select_related("user").filter(
                        discorduid=modal_interaction.user.id
                    ).first()
                    stock, _ = LootStock.objects.get_or_create(item=loot_item)
                    stock.quantity   = new_qty
                    stock.updated_by = du.user if du else None
                    stock.save()

                await _save()
                await self._refresh_embed()
                await modal_interaction.response.send_message(
                    f"✅ **{loot_item.name}** set to `{new_qty:,}`.", ephemeral=True
                )

        await interaction.response.send_modal(_SetQtyModal())

    # ── /qm additem — create a new LootItem ───────────────────────────────────

    @qm.command(name="additem", description="[QM] Add a new item to the org stockpile")
    @requires_django_perm(_MANAGE)
    async def qm_additem(self, interaction: discord.Interaction):
        CATEGORY_CHOICES = "\n".join([
            "CURRENCY · Currency (aUEC/Merits)",
            "WIKELO_CURRENCY · Wikelo Currencies",
            "WIKELO_MATERIAL · Wikelo Materials",
            "MONSTER_PART · Monster Parts",
            "MILITARY · Military Items",
            "COMPONENT · Wikelo Components",
            "WEAPON · Weapons",
            "LEGACY · Legacy Loot",
            "OTHER · Other",
        ])

        class _AddItemModal(discord.ui.Modal, title="Add Stockpile Item"):
            name = discord.ui.TextInput(
                label="Item Name",
                placeholder="e.g. Synthia Ore, Prison Merits, aUEC",
                max_length=150,
            )
            category = discord.ui.TextInput(
                label="Category (see list below)",
                placeholder="CURRENCY / WIKELO_MATERIAL / MONSTER_PART / etc.",
                max_length=30,
            )
            target = discord.ui.TextInput(
                label="Target Quantity (0 = unlimited)",
                default="0",
                max_length=15,
            )
            low_threshold = discord.ui.TextInput(
                label="Low Threshold (0 = none)",
                default="0",
                max_length=15,
            )
            location = discord.ui.TextInput(
                label="Location (STANTON / PYRO / NYX / UNIVERSAL)",
                default="UNIVERSAL",
                max_length=10,
            )

            async def on_submit(inner_self, modal_interaction: discord.Interaction):
                from app.quartermaster.models import LootItem
                cat = inner_self.category.value.strip().upper()
                valid_cats = [c[0] for c in LootItem.Category.choices]
                if cat not in valid_cats:
                    await modal_interaction.response.send_message(
                        f"❌ Invalid category `{cat}`.\nValid: {', '.join(valid_cats)}", ephemeral=True
                    )
                    return

                loc = inner_self.location.value.strip().upper()
                valid_locs = [l[0] for l in LootItem.Location.choices]
                is_universal = loc in ("UNIVERSAL", "")
                if not is_universal and loc not in valid_locs:
                    await modal_interaction.response.send_message(
                        f"❌ Invalid location `{loc}`. Use: STANTON, PYRO, NYX, or UNIVERSAL", ephemeral=True
                    )
                    return

                try:
                    tgt = int(inner_self.target.value.replace(",", ""))
                    low = int(inner_self.low_threshold.value.replace(",", ""))
                except ValueError:
                    await modal_interaction.response.send_message("❌ Target and threshold must be numbers.", ephemeral=True)
                    return

                @sync_to_async
                def _create():
                    item, created = LootItem.objects.get_or_create(
                        name=inner_self.name.value.strip(),
                        defaults={
                            "category":          cat,
                            "location":          LootItem.Location.STANTON if not is_universal else LootItem.Location.UNIVERSAL,
                            "location_agnostic": is_universal,
                            "target_qty":        tgt,
                            "low_threshold":     low,
                            "is_active":         True,
                        },
                    )
                    return item, created

                item_obj, created = await _create()
                if not created:
                    await modal_interaction.response.send_message(
                        f"⚠️ **{item_obj.name}** already exists.", ephemeral=True
                    )
                    return

                await modal_interaction.response.send_message(
                    f"✅ **{item_obj.name}** added to the stockpile under **{item_obj.get_category_display()}**.", ephemeral=True
                )

        await interaction.response.send_modal(_AddItemModal())

    # ── Physical stock autocomplete helpers ───────────────────────────────────

    async def _category_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        from app.quartermaster.models import StockItem
        existing = await sync_to_async(
            lambda: list(
                StockItem.objects.exclude(category="")
                .values_list("category", flat=True)
                .distinct()
                .order_by("category")
            )
        )()
        defaults = ["Weapons", "Armour", "Medical", "Tools", "Other"]
        all_cats = list(dict.fromkeys(defaults + existing))  # deduplicated, defaults first
        filtered = [c for c in all_cats if current.lower() in c.lower()]
        # Allow typing a brand-new category not yet in the list
        if current and current not in filtered:
            filtered.insert(0, current)
        return [app_commands.Choice(name=c, value=c) for c in filtered[:25]]

    async def _subcategory_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        from app.quartermaster.models import StockItem
        existing = await sync_to_async(
            lambda: list(
                StockItem.objects.exclude(subcategory="")
                .values_list("subcategory", flat=True)
                .distinct()
                .order_by("subcategory")
            )
        )()
        filtered = [c for c in existing if current.lower() in c.lower()]
        if current and current not in filtered:
            filtered.insert(0, current)
        return [app_commands.Choice(name=c, value=c) for c in filtered[:25]]

    @qm.command(name="add", description="Add a stock entry — individual unit, bulk SCU item, or crafted item with rolled stats")
    @app_commands.describe(
        name="Item name (creates catalogue entry if it doesn't exist yet)",
        mode="How this item is tracked",
        category="Category (select existing or type a new one)",
        subcategory="Sub-category (e.g. ore quality, item tier — optional)",
        craft_state="Initial state for crafted items",
        image="Screenshot to auto-scan crafted stats (optional)",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Individual Unit (gear, equipment)",     value="UNIT"),
        app_commands.Choice(name="Bulk / SCU Volume (ore, materials)",    value="BULK_SCU"),
        app_commands.Choice(name="Crafted Item (rolled stats + state)",   value="CRAFTED"),
    ])
    @app_commands.choices(craft_state=CRAFT_STATE_CHOICES)
    @app_commands.autocomplete(category=_category_autocomplete, subcategory=_subcategory_autocomplete)
    @requires_django_perm(_QM_MGR)
    async def qm_add(
        self,
        interaction: discord.Interaction,
        name: str,
        mode: str = "UNIT",
        category: str = "Other",
        subcategory: str = "",
        craft_state: str = "EXTERNAL",
        image: Optional[discord.Attachment] = None,
    ):
        from app.quartermaster.models import StockItem
        from app.discordauth.models import DiscordUser

        def _get_or_create_item():
            du, _ = DiscordUser.ensure_with_user(
                discorduid=interaction.user.id,
                access_token=None, refresh_token=None, access_token_expires=None,
            )
            return StockItem.objects.get_or_create(
                name=name,
                defaults={
                    "category": category,
                    "subcategory": subcategory,
                    "tracking_mode": mode,
                    "created_by": du.user,
                },
            )

        item_obj, item_created = await sync_to_async(_get_or_create_item)()

        # Validate image early if provided
        if image and (not image.content_type or not image.content_type.startswith("image/")):
            await interaction.response.send_message("Please upload a valid image file.", ephemeral=True)
            return

        # ── Bulk / SCU flow ───────────────────────────────────────────────────
        if mode == "BULK_SCU":
            if not image:
                modal = BulkSCUModal(item_obj.pk, self)
                await interaction.response.send_modal(modal)
                return

            # Image provided — scan for quantities
            await interaction.response.defer(ephemeral=True)
            try:
                img_bytes = await image.read()
                prompt = (
                    "Look ONLY at this image. Do not use any prior context.\n\n"
                    "This is a Star Citizen inventory screenshot. Your task:\n\n"
                    "CASE A — Cargo grid view: multiple box tiles each showing a number (e.g. 818.0, 855.0). "
                    "Each number is the ore/material unit count in that box. "
                    "Return every number you can read from each tile.\n\n"
                    "CASE B — Single item view: shows one item with an SCU fill bar (e.g. '0.270 SCU'). "
                    "Return only the SCU value shown on the fill bar (the yellow number). "
                    "Do NOT return the unit count (the white number next to the item name).\n\n"
                    "Known ship-minable materials: Agricium, Aluminium, Aslarite, Beryl, Bexalite, Borase, "
                    "Copper, Corundum, Gold, Hephaestanite, Ice, Iron, Laranite, Lindinium, Quratite, "
                    "Quantainium, Quartz, Riccite, Savrilium, Stileron, Silicon, Taranite, Tin, Titanium, "
                    "Torite, Tungsten. "
                    "Known hand/ROC-minable materials: Aphorite, Beradom, Carinite, Carinite Pure, Dolivine, "
                    "Feynmaline, Glacosite, Hadanite, Jaclium, Janalite, Sadaryx, Saldynium.\n\n"
                    "ore_quality: a number from 0 to 1000 shown near the item. If visible, return it as a number. "
                    "If not visible, return null.\n\n"
                    "Return ONLY this exact JSON in a markdown code block — no commentary:\n"
                    "```json\n"
                    "{\"material\": \"<item name>\", \"unit\": \"<units or SCU>\", \"ore_quality\": <number or null>, \"quantities\": [<number>, ...]}\n"
                    "```\n"
                    "Examples:\n"
                    "Cargo grid: {\"material\": \"Iron\", \"unit\": \"units\", \"ore_quality\": 750, \"quantities\": [818.0, 855.0, 908.0]}\n"
                    "Single item: {\"material\": \"Iron\", \"unit\": \"SCU\", \"ore_quality\": null, \"quantities\": [0.270]}"
                )
                raw_text    = await self._vision_scan(img_bytes, image.content_type, prompt)
                match = re.search(r"```json\s*(.*?)\s*```", raw_text, re.DOTALL)
                raw   = match.group(1) if match else raw_text.strip()
                parsed      = json.loads(raw)
                quantities  = [float(q) for q in parsed.get("quantities", []) if float(q) > 0]
                unit_label  = parsed.get("unit", "units")
                mat_name    = parsed.get("material", name)
                _oq         = parsed.get("ore_quality")
                ore_quality = str(int(_oq)) if _oq is not None else ""
            except Exception:
                log.exception("qm_add bulk scan: Claude scan failed")
                await interaction.followup.send(
                    "⚠️ Image scan failed. Enter the quantities manually instead.",
                    view=ScanFailedView(item_obj.pk, "BULK_SCU", cog=self),
                    ephemeral=True,
                )
                return

            if not quantities:
                await interaction.followup.send("❌ No quantities found in image. Enter manually without an image.", ephemeral=True)
                return

            new_item_note = f"✨ New item **{item_obj.name}** created.\n" if item_created else ""
            total = sum(quantities)
            fmt   = ".3f" if unit_label == "SCU" else ".1f"
            lines = "\n".join(f"• **{q:{fmt}}** {unit_label}" for q in quantities[:20])
            if len(quantities) > 20:
                lines += f"\n… and {len(quantities) - 20} more"
            embed = discord.Embed(
                title=f"🔍 Scan Complete — {mat_name}",
                description=f"{new_item_note}Found **{len(quantities)}** {'entry' if len(quantities) == 1 else 'entries'} totalling **{total:{fmt}} {unit_label}**.\nEach will be saved as a separate stock unit.",
                color=0x5865F2,
            )
            if ore_quality:
                embed.add_field(name="Ore Quality", value=ore_quality, inline=True)
            embed.add_field(name="Quantities", value=lines, inline=False)
            await interaction.followup.send(embed=embed, view=ConfirmBulkScanView(item_obj.pk, quantities, unit_label, ore_quality, self), ephemeral=True)
            return

        # ── Crafted / Unit flow (with optional image scan) ────────────────────
        _craft = craft_state if mode == "CRAFTED" else None

        if not image:
            modal = UnitStatsModal(item_obj.pk, self, craft_state=_craft)
            if item_created:
                modal.condition_notes.placeholder = f"New item '{item_obj.name}' created. Add notes if needed."
            await interaction.response.send_modal(modal)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            img_bytes = await image.read()

            if mode == "CRAFTED":
                prompt = (
                    "Analyze this Star Citizen item screenshot.\n"
                    "Extract:\n"
                    "1. The crafted/rolled stat names and their exact values (the green highlighted modifiers, e.g. '+24.09 %').\n"
                    "2. Item metadata visible in the panel: manufacturer, item_type, class, battery_size, rate_of_fire, effective_range, attachments.\n"
                    "Return ONLY valid JSON in a markdown code block:\n"
                    "```json\n"
                    "{\"stats\": {\"Impact Force\": \"+24.09 %\", \"Recoil Kick\": \"-38.72 %\"}, "
                    "\"metadata\": {\"manufacturer\": \"VOLT\", \"item_type\": \"Assault Rifle\", \"class\": \"Energy (Electron)\"}}\n"
                    "```"
                )
            else:
                prompt = (
                    "Analyse this Star Citizen item screenshot.\n"
                    "Extract all visible stat names and their numeric values.\n"
                    "Return ONLY a JSON object in a markdown code block:\n"
                    "```json\n{\"stats\": {\"Stat Name\": \"Value\", ...}}\n```"
                )

            raw_text = await self._vision_scan(img_bytes, image.content_type, prompt)
            match    = re.search(r"```json\s*(.*?)\s*```", raw_text, re.DOTALL)
            raw      = match.group(1) if match else raw_text.strip()
            parsed   = json.loads(raw)
            stats    = parsed.get("stats", {})
            metadata = parsed.get("metadata", {})
        except Exception:
            log.exception("qm_add: vision scan failed")
            await interaction.followup.send(
                "⚠️ Image scan failed. Enter the stats manually instead.",
                view=ScanFailedView(item_obj.pk, mode, craft_state=_craft, cog=self),
                ephemeral=True,
            )
            return

        # Persist item-level metadata from scan
        if metadata:
            item_obj.sc_metadata = {**item_obj.sc_metadata, **{k: v for k, v in metadata.items() if v}}
            await item_obj.asave(update_fields=["sc_metadata"])

        new_item_note = f"✨ New item **{item_obj.name}** created.\n" if item_created else ""
        stats_lines   = "\n".join(f"• **{k}**: {v}" for k, v in stats.items()) or "*No stats extracted — click Edit Stats to enter manually*"
        meta_lines    = "\n".join(f"• **{k}**: {v}" for k, v in metadata.items() if v) or None
        embed = discord.Embed(
            title=f"🔍 Scan Complete — {item_obj.name}",
            description=f"{new_item_note}Extracted **{len(stats)}** stat(s). Confirm or edit before saving.",
            color=0x5865F2,
        )
        embed.add_field(name="Rolled Stats", value=stats_lines, inline=False)
        if meta_lines:
            embed.add_field(name="Item Metadata", value=meta_lines, inline=False)
        await interaction.followup.send(
            embed=embed,
            view=ConfirmScanView(item_obj.pk, item_obj.name, stats, self, craft_state=_craft),
            ephemeral=True,
        )

    # ── /qm inventory — replaces list + stock + units ────────────────────────

    @qm.command(name="inventory", description="Browse stock catalogue, or drill into a specific item's units")
    @app_commands.describe(item="Leave blank for full catalogue, or type an item name to see its units")
    @requires_django_perm(_QM_VIEW)
    async def qm_inventory(self, interaction: discord.Interaction, item: str = ""):
        from app.quartermaster.models import StockItem, StockUnit
        from django.db.models import Count, Q, Sum
        await interaction.response.defer(ephemeral=True)

        if not item:
            items = [i async for i in StockItem.objects.annotate(
                avail=Count("units", filter=Q(units__status=StockUnit.Status.AVAILABLE)),
                total=Count("units", filter=~Q(units__status=StockUnit.Status.RETIRED)),
                scu_total=Sum("units__scu_volume", filter=~Q(units__status=StockUnit.Status.RETIRED)),
            ).order_by("category", "name")]
            if not items:
                await interaction.followup.send("No items in catalogue yet. Use `/qm add`.", ephemeral=True)
                return
            MODE_ICON = {StockItem.TrackingMode.UNIT: "📦", StockItem.TrackingMode.BULK_SCU: "⛏️", StockItem.TrackingMode.CRAFTED: "⚒️"}
            cats: dict[str, list] = {}
            for i in items:
                cats.setdefault(i.category or "Uncategorised", []).append(i)
            embed = discord.Embed(title="📦 Stock Catalogue", color=0x5865F2)
            for cat, cat_items in cats.items():
                lines = []
                for i in cat_items:
                    icon  = MODE_ICON.get(i.tracking_mode, "📦")
                    avail = i.avail or 0
                    total = i.total or 0
                    low   = "⚠️ " if avail <= i.low_stock_threshold else ""
                    if i.tracking_mode == StockItem.TrackingMode.BULK_SCU:
                        lines.append(f"{low}{icon} **{i.name}** — {float(i.scu_total or 0):,.3f} SCU ({total} containers)")
                    else:
                        lines.append(f"{low}{icon} **{i.name}** — {avail}/{total} available")
                embed.add_field(name=cat, value="\n".join(lines) or "—", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        item_obj = (
            await StockItem.objects.filter(pk=int(item)).afirst() if item.isdigit()
            else await StockItem.objects.filter(name__iexact=item).afirst()
                 or await StockItem.objects.filter(name__icontains=item).afirst()
        )
        if not item_obj:
            await interaction.followup.send(f"❌ Item **{item}** not found.", ephemeral=True)
            return

        units = [u async for u in item_obj.units.select_related("current_holder")
                 .exclude(status=StockUnit.Status.RETIRED).order_by("status", "-added_at")]

        avail = sum(1 for u in units if u.status == StockUnit.Status.AVAILABLE)
        low   = avail <= item_obj.low_stock_threshold
        cat_display = " › ".join(filter(None, [item_obj.category, item_obj.subcategory]))
        embed = discord.Embed(
            title=f"{'⚠️ ' if low else ''}📦 {item_obj.name}",
            description=f"**{avail}** available · **{len(units)}** in circulation · {cat_display or 'No category'}",
            color=0x22C55E if not low else 0xFFC107,
        )
        if item_obj.stats:
            embed.add_field(name="Item Stats", value="\n".join(f"**{k}:** {v}" for k, v in item_obj.stats.items()), inline=True)
        for unit in units[:20]:
            holder = unit.current_holder.display_name if unit.current_holder_id else "—"
            if item_obj.tracking_mode == "BULK_SCU":
                detail = f"**{unit.scu_volume or unit.sc_metadata.get('quantity', 0)} SCU**"
                if unit.sc_metadata.get("ore_quality"):
                    detail += f" · Q: **{unit.sc_metadata['ore_quality']}**"
            else:
                stats_str = ", ".join(f"{k}: {v}" for k, v in unit.stats.items()) or "No stats"
                detail = f"`{stats_str}`"
                if unit.craft_state:
                    detail += f"\nState: **{dict(StockUnit.CraftState.choices).get(unit.craft_state, unit.craft_state)}**"
            embed.add_field(
                name=f"{_STATUS_EMOJI.get(unit.status, '⚪')} #{unit.pk} · {unit.get_status_display()}",
                value=f"{detail}\nHolder: **{holder}**",
                inline=False,
            )
        if len(units) > 20:
            embed.add_field(name="…", value=f"*{len(units) - 20} more — use the web portal for the full list*", inline=False)
        if low:
            embed.set_footer(text=f"⚠️ Low stock — threshold is {item_obj.low_stock_threshold} unit(s)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @qm_inventory.autocomplete("item")
    async def inventory_item_ac(self, interaction: discord.Interaction, current: str):
        return await _stock_item_autocomplete(interaction, current)

    # ── /qm manage — replaces assign + return + lost + remove ────────────────

    @qm.command(name="manage", description="[QM] Assign, return, mark lost, or retire a stock unit")
    @app_commands.describe(item="Item name — then select the unit to manage from the list")
    @requires_django_perm(_QM_ASGN)
    async def qm_manage(self, interaction: discord.Interaction, item: str):
        from app.quartermaster.models import StockItem, StockUnit
        await interaction.response.defer(ephemeral=True)

        item_obj = (
            await StockItem.objects.filter(pk=int(item)).afirst() if item.isdigit()
            else await StockItem.objects.filter(name__iexact=item).afirst()
                 or await StockItem.objects.filter(name__icontains=item).afirst()
        )
        if not item_obj:
            await interaction.followup.send(f"❌ Item **{item}** not found.", ephemeral=True)
            return

        units = [u async for u in item_obj.units.select_related("current_holder")
                 .exclude(status=StockUnit.Status.RETIRED).order_by("status", "-added_at")[:25]]
        if not units:
            await interaction.followup.send(f"No active units for **{item_obj.name}**.", ephemeral=True)
            return

        unit_data = []
        for u in units:
            holder     = u.current_holder.display_name if u.current_holder_id else "—"
            stats_hint = ", ".join(f"{k}: {v}" for k, v in list(u.stats.items())[:2]) or "no stats"
            unit_data.append({
                "pk": u.pk, "uid": str(u.uid),
                "status": u.status, "status_display": u.get_status_display(),
                "holder": holder, "stats_hint": stats_hint[:50],
                "item_name": item_obj.name,
            })

        await interaction.followup.send(
            f"📦 **{item_obj.name}** — select a unit to manage:",
            view=_UnitSelectView(unit_data, item_obj.name, self),
            ephemeral=True,
        )

    @qm_manage.autocomplete("item")
    async def manage_item_ac(self, interaction: discord.Interaction, current: str):
        return await _stock_item_autocomplete(interaction, current)

    @qm.command(name="craft", description="Show crafting requirements for an item")
    @app_commands.describe(item="Item name (autocomplete)")
    @app_commands.autocomplete(item=_blueprint_autocomplete)
    @requires_django_perm(_QM_VIEW)
    async def qm_craft(self, interaction: discord.Interaction, item: str):
        await interaction.response.defer(ephemeral=True)
        stock_item, bp = await _get_blueprint_for_item(item)

        if not stock_item:
            await interaction.followup.send(f"No item found matching **{item}**.", ephemeral=True)
            return

        if not bp:
            await interaction.followup.send(
                f"No craft blueprint defined for **{stock_item.name}**. QMs can add one via the admin panel.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"⚒️ {stock_item.name}",
            description=f"**{bp.get_item_type_display()}** craft requirements",
            color=discord.Color.blue(),
        )

        mats = list(bp.materials.order_by("sort_order", "pk"))
        if mats:
            lines = []
            for mat in mats:
                label = mat.material_name
                if mat.constraint_group:
                    label += f"[{mat.constraint_group}]"
                temp   = f"`{mat.temp_display}`"
                yld    = f"`{mat.yield_display}`"
                lines.append(f"**{label}** — Temp: {temp} · Yield: {yld}")
            embed.add_field(name="Materials", value="\n".join(lines), inline=False)

        constraints = list(bp.constraints.all())
        if constraints:
            notes = []
            for c in constraints:
                text = c.note if c.note else f"Combined total for [{c.group_label}] must equal {c.combined_total}"
                notes.append(text)
            embed.add_field(name="Constraints", value="\n".join(notes), inline=False)

        if bp.notes:
            embed.add_field(name="Notes", value=bp.notes, inline=False)

        embed.set_footer(text="BlightVeil Craft Guide")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @qm.command(name="craftable", description="[QM] Show which items can be crafted from current ore stock")
    @requires_django_perm(_QM_VIEW)
    async def qm_craftable(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        bps, data = await _check_craftable_stock()

        if not data:
            await interaction.followup.send("No craft blueprints configured yet.", ephemeral=True)
            return

        can_craft   = [d for d in data.values() if d["can_craft"]]
        cant_craft  = [d for d in data.values() if not d["can_craft"]]

        embed = discord.Embed(
            title="⚒️ Craft Availability",
            description=f"**{len(can_craft)}** item(s) craftable · **{len(cant_craft)}** need more materials",
            color=discord.Color.green() if can_craft else discord.Color.orange(),
            timestamp=timezone.now(),
        )

        if can_craft:
            lines = [f"✅ **{d['item_name']}** ({d['item_type']})" for d in can_craft]
            embed.add_field(name="Can Craft", value="\n".join(lines), inline=False)

        if cant_craft:
            lines = []
            for d in cant_craft[:10]:
                missing = [f"  — {m[0]}: {m[1]:.3f}/{m[2]:.3f} SCU" for m in d["materials"] if not m[3]]
                lines.append(f"❌ **{d['item_name']}**\n" + "\n".join(missing))
            embed.add_field(name="Missing Materials", value="\n".join(lines)[:1024], inline=False)

        embed.set_footer(text=f"Full details: {_site_url()}loot/craft/")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @qm.command(name="recordcraft", description="[QM] Record a completed craft — adds the item to stock")
    @app_commands.describe(
        item="The item that was crafted",
        craft_state="Initial state of the crafted unit",
    )
    @app_commands.autocomplete(item=_blueprint_autocomplete)
    @app_commands.choices(craft_state=CRAFT_STATE_CHOICES)
    @requires_django_perm(_QM_MGR)
    async def qm_recordcraft(self, interaction: discord.Interaction, item: str, craft_state: str = "HELD_O"):
        stock_item, bp = await _get_blueprint_for_item(item)
        if not stock_item:
            await interaction.response.send_message(f"❌ No item found matching **{item}**.", ephemeral=True)
            return
        if not bp:
            await interaction.response.send_message(
                f"❌ No craft blueprint for **{stock_item.name}**.", ephemeral=True
            )
            return
        # Open the stats modal — same flow as /qm add CRAFTED
        await interaction.response.send_modal(
            UnitStatsModal(stock_item.pk, self, craft_state=craft_state)
        )

    @qm.command(name="equipembed", description="[QM] Post / refresh the equipment request panel in the configured channel")
    @requires_django_perm(_QM_MGR)
    async def qm_equipembed(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel = await self._get_equip_channel()
        if not channel:
            await interaction.followup.send(
                "❌ `EquipmentChannelID` preference is not set. Configure it in the admin panel.", ephemeral=True
            )
            return
        await self._refresh_equipment_embed()
        await interaction.followup.send(f"✅ Equipment panel refreshed in {channel.mention}.", ephemeral=True)

    @qm.command(name="member", description="View all stock currently held by a member")
    @app_commands.describe(member="The member to look up")
    @requires_django_perm(_QM_VIEW)
    async def qm_member(self, interaction: discord.Interaction, member: discord.Member):
        from app.quartermaster.models import StockUnit
        from app.unifieduser.models import OrgPlayer
        await interaction.response.defer(ephemeral=True)
        player = await OrgPlayer.objects.filter(discorduser__discorduid=member.id).afirst()
        if not player:
            await interaction.followup.send(f"❌ {member.mention} doesn't have a linked org profile.", ephemeral=True)
            return

        units = [u async for u in StockUnit.objects.filter(current_holder=player, status=StockUnit.Status.ASSIGNED).select_related("item").order_by("item__name")]
        embed = discord.Embed(title=f"🎒 Holdings — {member.display_name}", description=f"**{len(units)}** unit(s) currently assigned", color=0x5865F2)
        if not units:
            embed.description = "No items currently assigned to this member."
        else:
            for unit in units:
                stats = ", ".join(f"{k}: {v}" for k, v in unit.stats.items()) or "No stats"
                embed.add_field(name=f"📦 {unit.item.name}  ·  {unit.uid}", value=f"`{stats}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /qm merit — member submits aUEC / merit request (improved: kind dropdown) ─

    @qm.command(name="merit", description="Submit an aUEC payout or merit award request")
    @app_commands.describe(kind="Type of request")
    @app_commands.choices(kind=[
        app_commands.Choice(name="🏅 Merit Award",  value="MERIT"),
        app_commands.Choice(name="💰 aUEC Payout",  value="MONEY"),
    ])
    @requires_django_perm("loot_tracker.submit_merit_request")
    async def qm_merit(self, interaction: discord.Interaction, kind: str):
        await interaction.response.send_modal(_MeritSubmitModal(kind))

    # ── /qm review — replaces merit_review + merit_action ────────────────────

    @qm.command(name="review", description="[QM] Review and action pending merit / aUEC requests")
    @requires_django_perm("loot_tracker.review_merit_request")
    async def qm_review(self, interaction: discord.Interaction):
        from app.quartermaster.models import MeritRequest
        await interaction.response.defer(ephemeral=True)

        @sync_to_async
        def _get_pending():
            return list(
                MeritRequest.objects.filter(status=MeritRequest.Status.PENDING)
                .select_related("requester")
                .order_by("submitted_at")[:10]
            )

        pending = await _get_pending()
        if not pending:
            await interaction.followup.send("✅ No pending merit / aUEC requests.", ephemeral=True)
            return

        embed = discord.Embed(title="📋 Pending Merit / aUEC Requests", color=0xF59E0B)
        for req in pending:
            kind   = "aUEC" if req.kind == MeritRequest.Kind.MONEY else "Merits"
            amount = f"{req.amount:,} {kind}"
            try:
                name = req.requester.display_name
            except Exception:
                name = req.requester.username
            embed.add_field(
                name=f"#{req.pk} · {name} · {amount}",
                value=req.title or req.reason[:80] or "—",
                inline=False,
            )
        await interaction.followup.send(embed=embed, view=_MeritReviewView(pending), ephemeral=True)

    # ── Slash commands: merits (member-facing) ────────────────────────────────

    @app_commands.command(name="prison_time", description="Convert SC prison time to merits and submit a request")
    async def prison_time_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_PrisonTimeModal())

    # ── Slash commands: weapon tracker ───────────────────────────────────────

    @qm.command(name="weapon", description="Add a crafted weapon to the inventory (image = auto-scan, no image = manual)")
    @requires_django_perm("loot_tracker.can_submit_new_weapon")
    async def qm_weapon(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None):
        if not image:
            modal = WeaponDataModal()
            modal.cog = self
            await interaction.response.send_modal(modal)
            return

        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message("Please upload a valid image file.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            image_bytes = await image.read()
            prompt = (
                "Analyze this game inventory screenshot.\n"
                "1. Extract the weapon name (first line of text top-left).\n"
                "2. Extract the crafted stats (green/red percentage modifiers).\n"
                "Return as JSON in a markdown code block:\n"
                "```json\n{\"weapon_name\": \"Name\", \"crafted_stats\": {\"Stat\": \"Value\"}}\n```"
            )
            raw_text    = await self._vision_scan(image_bytes, image.content_type, prompt)
            match       = re.search(r"```json\s*(.*?)\s*```", raw_text, re.DOTALL)
            json_string = match.group(1) if match else raw_text.strip()
            data        = json.loads(json_string)
            extracted_name  = data.get("weapon_name", "Unknown Weapon")
            extracted_stats = data.get("crafted_stats", {})
        except Exception:
            log.exception("scan_weapon: vision scan failed")
            modal = WeaponDataModal()
            modal.cog = self
            # Can't send_modal after defer — tell user to retry without image
            await interaction.followup.send("⚠️ Image scan failed. Re-run `/qm weapon` without an image to enter data manually.", ephemeral=True)
            return

        view = ReviewDataView(weapon_name=extracted_name, stats_dict=extracted_stats, cog=self)
        await interaction.followup.send(
            content=f"Scan complete! Found **{extracted_name}** with {len(extracted_stats)} stats. Click below to verify and save.",
            view=view,
            ephemeral=True,
        )

    # ── on_ready ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        await self._refresh_embed()
        await self._refresh_equipment_embed()
        guild_id = int(await aget_global_preference(BotGuildID.import_path) or 0)
        guild    = self.client.get_guild(guild_id)
        if guild:
            try:
                await self.refresh_inventory_listing(guild)
            except discord.HTTPException as e:
                log.warning("[LOOT COG] refresh_inventory_listing skipped on startup: %s", e)

    # ── Error handler ─────────────────────────────────────────────────────────

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingDjangoPermission):
            msg = f"🛡️ **Permission denied:** you need `{error.missing_perm}`."
        elif isinstance(error, AccountNotLinked):
            msg = "🎮 Your Discord account isn't linked to an org profile yet."
        else:
            msg = "❌ An unexpected error occurred."
            log.exception("LootCog error: %s", error)

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(client):
    await client.add_cog(LootCog(client))
