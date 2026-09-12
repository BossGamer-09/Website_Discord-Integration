"""
app/quartermaster/views.py

Unified web views for:
  - Loot stockpile (LootItem / LootStock / LootRequest)
  - Physical equipment (StockItem / StockUnit / StockAssignment)
  - Merit / money requests (MeritRequest)
"""
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.db.models import Count, Q
from django.utils import timezone

from app.preferences.utils import get_global_preference
from .models import (
    LootItem, LootStock, LootRequest,
    StockItem, StockUnit, StockAssignment,
    CraftBlueprint, CraftMaterial,
    EquipmentRequest,
    MeritRequest,
)
from .preferences import LootQMGroupIDs
try:
    from app.merits.prison_time import parse_prison_time
except ImportError:
    from app.quartermaster.prison_time import parse_prison_time


def _perm(request, codename):
    return request.user.has_perm(f"loot_tracker.{codename}")


def _require(request, codename):
    if not _perm(request, codename):
        return render(request, "quartermaster/access_denied.html", status=403)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# LOOT STOCKPILE VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def loot_overview(request):
    deny = _require(request, "view_loot")
    if deny:
        return deny

    loot_items = (
        LootItem.objects
        .filter(is_active=True)
        .select_related("stock")
        .order_by("category", "name")
    )
    stock_items = (
        StockItem.objects
        .prefetch_related("units")
        .annotate(
            available=Count("units", filter=Q(units__status=StockUnit.Status.AVAILABLE)),
            assigned=Count("units",  filter=Q(units__status=StockUnit.Status.ASSIGNED)),
        )
        .order_by("category", "name")
    ) if _perm(request, "view_stock") else StockItem.objects.none()

    ctx = {
        "loot_items":  loot_items,
        "stock_items": stock_items,
        "Category":    LootItem.Category,
        "can_manage":  _perm(request, "manage_loot"),
        "can_assign":  _perm(request, "assign_stock"),
        "can_stock":   _perm(request, "view_stock"),
    }
    return render(request, "quartermaster/loot_overview.html", ctx)


@login_required
def loot_requests(request):
    deny = _require(request, "manage_loot")
    if deny:
        return deny

    qs = (
        LootRequest.objects
        .select_related("item", "requester", "reviewed_by")
        .order_by("status", "-submitted_at")
    )
    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(status=status_filter)

    ctx = {
        "requests":      qs[:200],
        "status_filter": status_filter,
        "Status":        LootRequest.Status,
        "Direction":     LootRequest.Direction,
    }
    return render(request, "quartermaster/loot_requests.html", ctx)


# ─────────────────────────────────────────────────────────────────────────────
# STOCK (EQUIPMENT) VIEWS  — migrated from app.inventory
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def stock_overview(request):
    deny = _require(request, "view_stock")
    if deny:
        return deny

    items = (
        StockItem.objects
        .prefetch_related("units")
        .annotate(
            available=Count("units", filter=Q(units__status=StockUnit.Status.AVAILABLE)),
            assigned=Count("units",  filter=Q(units__status=StockUnit.Status.ASSIGNED)),
        )
        .order_by("category", "name")
    )
    ctx = {
        "items":      items,
        "can_manage": _perm(request, "manage_stock"),
        "can_assign": _perm(request, "assign_stock"),
    }
    return render(request, "quartermaster/stock_overview.html", ctx)


@login_required
def item_detail(request, item_slug):
    deny = _require(request, "view_stock")
    if deny:
        return deny

    item  = get_object_or_404(StockItem, slug=item_slug)
    units = (
        item.units
        .exclude(status=StockUnit.Status.RETIRED)
        .select_related("current_holder", "added_by")
        .order_by("status", "-added_at")
    )
    bp_materials   = []
    bp_constraints = []
    try:
        blueprint      = item.blueprint
        bp_materials   = list(blueprint.materials.order_by("sort_order", "pk"))
        bp_constraints = list(blueprint.constraints.all())
    except CraftBlueprint.DoesNotExist:
        blueprint = None

    ctx = {
        "item":           item,
        "units":          units,
        "blueprint":      blueprint,
        "bp_materials":   bp_materials,
        "bp_constraints": bp_constraints,
        "can_manage":     _perm(request, "manage_stock"),
        "can_assign":     _perm(request, "assign_stock"),
        "Status":         StockUnit.Status,
    }
    return render(request, "quartermaster/item_detail.html", ctx)


@login_required
def adjust_stock(request):
    deny = _require(request, "manage_loot")
    if deny:
        return deny

    items = (
        LootItem.objects
        .filter(is_active=True)
        .select_related("stock")
        .order_by("category", "name")
    )

    if request.method == "POST":
        updated = 0
        for item in items:
            raw = request.POST.get(f"qty_{item.pk}", "").strip()
            if raw == "":
                continue
            try:
                new_qty = int(raw.replace(",", "").replace(".", ""))
                if new_qty < 0:
                    raise ValueError
            except ValueError:
                messages.error(request, f"{item.name}: invalid value '{raw}'")
                continue
            try:
                current = item.stock.quantity
            except LootStock.DoesNotExist:
                current = 0
            if new_qty != current:
                stock, _ = LootStock.objects.get_or_create(item=item)
                stock.quantity   = new_qty
                stock.updated_by = request.user
                stock.save()
                updated += 1
        if updated:
            messages.success(request, f"Updated {updated} item{'s' if updated != 1 else ''}.")
        return redirect("quartermaster:overview")

    ctx = {"items": items, "Category": LootItem.Category}
    return render(request, "quartermaster/adjust_stock.html", ctx)


def _compute_craftable(blueprints):
    """For each blueprint, annotate each material with have/need and overall can_craft flag."""
    from decimal import Decimal
    from django.db.models import Sum, Q

    for bp in blueprints:
        bp_can_craft = True
        for mat in bp.mat_list:
            # Match StockItem by name (case-insensitive contains)
            qs = StockUnit.objects.filter(
                item__name__icontains=mat.material_name,
                item__tracking_mode=StockItem.TrackingMode.BULK_SCU,
            ).exclude(status=StockUnit.Status.RETIRED)

            # Filter by ore quality
            if mat.min_temp > 0:
                filtered = []
                for u in qs.select_related("item"):
                    q = u.sc_metadata.get("ore_quality")
                    if q is None:
                        continue
                    try:
                        q_int = int(q)
                    except (ValueError, TypeError):
                        continue
                    if mat.temp_is_floor and q_int >= mat.min_temp:
                        filtered.append(u.scu_volume or Decimal("0"))
                    elif not mat.temp_is_floor and q_int == mat.min_temp:
                        filtered.append(u.scu_volume or Decimal("0"))
                have = sum(filtered, Decimal("0"))
            else:
                result = qs.aggregate(total=Sum("scu_volume"))
                have = result["total"] or Decimal("0")

            mat.have = have
            mat.need = mat.yield_min
            mat.ok   = have >= mat.yield_min
            if not mat.ok:
                bp_can_craft = False

        bp.can_craft = bp_can_craft and bool(bp.mat_list)


@login_required
def craft_guide(request):
    deny = _require(request, "view_stock")
    if deny:
        return deny

    blueprints = list(
        CraftBlueprint.objects
        .select_related("stock_item")
        .prefetch_related("materials", "constraints")
        .order_by("item_type", "stock_item__name")
    )
    for bp in blueprints:
        bp.mat_list         = list(bp.materials.order_by("sort_order", "pk"))
        bp.constraint_list  = list(bp.constraints.all())

    _compute_craftable(blueprints)

    grouped = {}
    for bp in blueprints:
        label = bp.get_item_type_display()
        grouped.setdefault(label, []).append(bp)

    ctx = {
        "grouped":    grouped,
        "can_manage": _perm(request, "manage_stock"),
        "ItemType":   CraftBlueprint.ItemType,
    }
    return render(request, "quartermaster/craft_guide.html", ctx)


@login_required
def equipment_requests(request):
    deny = _require(request, "manage_stock")
    if deny:
        return deny

    status_filter = request.GET.get("status", "")
    qs = (
        EquipmentRequest.objects
        .select_related("stock_item", "requester", "reviewed_by", "assigned_unit")
        .order_by("status", "-submitted_at")
    )
    if status_filter:
        qs = qs.filter(status=status_filter)

    ctx = {
        "requests":      qs[:200],
        "status_filter": status_filter,
        "Status":        EquipmentRequest.Status,
        "can_manage":    _perm(request, "manage_stock"),
        "can_assign":    _perm(request, "assign_stock"),
    }
    return render(request, "quartermaster/equipment_requests.html", ctx)


@login_required
@require_POST
def assign_unit(request, item_slug, unit_uid):
    deny = _require(request, "assign_stock")
    if deny:
        return deny

    item = get_object_or_404(StockItem, slug=item_slug)
    unit = get_object_or_404(StockUnit, uid=unit_uid, item=item)
    action = request.POST.get("action", "")

    if action == "assign":
        recipient_name = request.POST.get("recipient_name", "").strip()
        notes          = request.POST.get("notes", "").strip()
        if not recipient_name:
            messages.error(request, "Recipient name is required.")
            return redirect("quartermaster:item_detail", item_pk=unit.item_id)

        from app.unifieduser.models import OrgPlayer
        recipient = OrgPlayer.objects.filter(
            Q(displaynamesearchcache__display_name__iexact=recipient_name) |
            Q(username__iexact=recipient_name)
        ).first()

        unit.status         = StockUnit.Status.ASSIGNED
        unit.current_holder = recipient
        unit.save(update_fields=["status", "current_holder", "updated_at"])
        StockAssignment.objects.create(
            unit=unit, action=StockAssignment.Action.ASSIGNED,
            recipient=recipient,
            recipient_name=recipient_name if not recipient else "",
            notes=notes, actioned_by=request.user,
        )
        messages.success(request, f"Assigned {unit.item.name} to {recipient_name}.")

    elif action == "return":
        notes       = request.POST.get("notes", "").strip()
        prev_holder = unit.current_holder
        unit.status         = StockUnit.Status.AVAILABLE
        unit.current_holder = None
        unit.save(update_fields=["status", "current_holder", "updated_at"])
        StockAssignment.objects.create(
            unit=unit, action=StockAssignment.Action.RETURNED,
            recipient=prev_holder, notes=notes, actioned_by=request.user,
        )
        messages.success(request, f"{unit.item.name} marked as returned.")

    elif action == "lost":
        notes       = request.POST.get("notes", "").strip()
        prev_holder = unit.current_holder
        unit.status         = StockUnit.Status.LOST
        unit.current_holder = None
        unit.save(update_fields=["status", "current_holder"])
        StockAssignment.objects.create(
            unit=unit, action=StockAssignment.Action.LOST,
            recipient=prev_holder, notes=notes, actioned_by=request.user,
        )
        messages.success(request, f"{unit.item.name} marked as lost.")

    else:
        messages.error(request, "Unknown action.")

    return redirect("quartermaster:item_detail", item_slug=item.slug)


@login_required
def assignment_log(request, item_slug=None):
    deny = _require(request, "view_stock")
    if deny:
        return deny

    qs = (
        StockAssignment.objects
        .select_related("unit__item", "recipient", "actioned_by")
        .order_by("-actioned_at")
    )
    item = None
    if item_slug:
        item = get_object_or_404(StockItem, slug=item_slug)
        qs   = qs.filter(unit__item=item)

    ctx = {"log": qs[:200], "item": item}
    return render(request, "quartermaster/assignment_log.html", ctx)


# ─────────────────────────────────────────────────────────────────────────────
# MERIT / MONEY REQUEST VIEWS  — migrated from app.merits
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def merit_list(request):
    deny = _require(request, "view_merit_requests")
    if deny:
        return deny

    status_filter = request.GET.get("status", "")
    kind_filter   = request.GET.get("kind", "")
    qs = MeritRequest.objects.select_related("requester", "reviewed_by", "linked_item").order_by("status", "-submitted_at")
    if status_filter:
        qs = qs.filter(status=status_filter)
    if kind_filter:
        qs = qs.filter(kind=kind_filter)

    ctx = {
        "requests":      qs[:200],
        "status_filter": status_filter,
        "kind_filter":   kind_filter,
        "Status":        MeritRequest.Status,
        "Kind":          MeritRequest.Kind,
        "can_review":    _perm(request, "review_merit_request"),
        "can_fulfill":   _perm(request, "fulfill_merit_request"),
    }
    return render(request, "quartermaster/merit_list.html", ctx)


@login_required
def my_merit_requests(request):
    deny = _require(request, "submit_merit_request")
    if deny:
        return deny

    qs = MeritRequest.objects.filter(requester=request.user).select_related("linked_item").order_by("-submitted_at")
    ctx = {"requests": qs, "Status": MeritRequest.Status, "Kind": MeritRequest.Kind}
    return render(request, "quartermaster/my_merit_requests.html", ctx)


@login_required
def merit_friends_gate(request):
    deny = _require(request, "submit_merit_request")
    if deny:
        return deny

    from .preferences import MeritFriendCheckUsername, MeritFriendCheckRSIUrl
    username = get_global_preference(MeritFriendCheckUsername.import_path) or ""
    rsi_url  = get_global_preference(MeritFriendCheckRSIUrl.import_path) or ""

    if not username:
        return redirect("quartermaster:merit_submit")

    if request.session.get("merits_friends_confirmed"):
        return redirect("quartermaster:merit_submit")

    if request.method == "POST":
        if request.POST.get("confirmed") == "yes":
            request.session["merits_friends_confirmed"] = True
            return redirect("quartermaster:merit_submit")
        return render(request, "quartermaster/merit_friends_gate.html", {"username": username, "rsi_url": rsi_url, "denied": True})

    return render(request, "quartermaster/merit_friends_gate.html", {"username": username, "rsi_url": rsi_url, "denied": False})


@login_required
def merit_submit(request):
    deny = _require(request, "submit_merit_request")
    if deny:
        return deny

    if request.method == "POST":
        kind             = request.POST.get("kind", "").strip()
        raw              = request.POST.get("amount", "").strip()
        rsi_handle       = request.POST.get("rsi_handle", "").strip()
        during_event_raw = request.POST.get("during_event", "").strip()
        purchase_for     = request.POST.get("purchase_for", "").strip()

        errors = []
        if kind not in dict(MeritRequest.Kind.choices):
            errors.append("Invalid request type.")
        if not rsi_handle:
            errors.append("RSI handle is required.")
        if kind == MeritRequest.Kind.MERIT and during_event_raw not in ("yes", "no"):
            errors.append("Please select Yes or No for the event question.")
        if kind == MeritRequest.Kind.MONEY and not purchase_for:
            errors.append("Please specify what the aUEC is for.")

        amount = 0
        if kind == MeritRequest.Kind.MONEY:
            try:
                amount = int(raw)
                if amount < 0:
                    raise ValueError
            except ValueError:
                errors.append("aUEC amount must be a whole number.")
        else:
            if raw:
                if raw.isdigit():
                    amount = int(raw)
                else:
                    parsed = parse_prison_time(raw)
                    if parsed is None:
                        errors.append("Enter a number or a prison time like 1h 30m or 45m.")
                    else:
                        amount = parsed

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "quartermaster/merit_submit.html", {"Kind": MeritRequest.Kind, "post": request.POST})

        during_event = None
        if kind == MeritRequest.Kind.MERIT:
            during_event = during_event_raw == "yes"
            event_label  = "during event" if during_event else "outside event"
            title  = f"Merit — {rsi_handle} ({event_label})"
            reason = f"RSI Handle: {rsi_handle} | During event: {'Yes' if during_event else 'No'}"
        else:
            title  = f"aUEC — {rsi_handle} — {purchase_for}"
            reason = f"RSI Handle: {rsi_handle} | Purchasing: {purchase_for}"

        MeritRequest.objects.create(
            requester=request.user, kind=kind, title=title, reason=reason,
            amount=amount, rsi_handle=rsi_handle, during_event=during_event,
            purchase_for=purchase_for if kind == MeritRequest.Kind.MONEY else "",
        )
        messages.success(request, "Request submitted! Staff will review it shortly.")
        return redirect("quartermaster:my_merit_requests")

    return render(request, "quartermaster/merit_submit.html", {"Kind": MeritRequest.Kind})


@login_required
def merit_detail(request, uid):
    obj = get_object_or_404(MeritRequest, uid=uid)
    if obj.requester != request.user and not _perm(request, "view_merit_requests"):
        return render(request, "quartermaster/access_denied.html", status=403)

    ctx = {
        "obj":        obj,
        "Status":     MeritRequest.Status,
        "can_review": _perm(request, "review_merit_request"),
    }
    return render(request, "quartermaster/merit_detail.html", ctx)


@login_required
@require_POST
def merit_process(request, uid):
    deny = _require(request, "review_merit_request")
    if deny:
        return deny

    obj    = get_object_or_404(MeritRequest, uid=uid)
    action = request.POST.get("action", "")
    note   = request.POST.get("reviewer_note", "").strip()
    amount = request.POST.get("amount", "").strip()

    if action not in ("fulfill", "deny"):
        messages.error(request, "Unknown action.")
        return redirect("quartermaster:merit_detail", pk=pk)

    if amount:
        try:
            obj.amount = int(amount)
        except ValueError:
            messages.error(request, "Invalid amount.")
            return redirect("quartermaster:merit_detail", pk=pk)

    now = timezone.now()
    if action == "fulfill":
        obj.status       = MeritRequest.Status.FULFILLED
        obj.fulfilled_at = now
    else:
        obj.status = MeritRequest.Status.DENIED

    obj.reviewed_by   = request.user
    obj.reviewer_note = note
    obj.reviewed_at   = now
    obj.save()

    messages.success(request, f"Request {'fulfilled' if action == 'fulfill' else 'denied'}.")
    return redirect("quartermaster:merit_detail", uid=obj.uid)
