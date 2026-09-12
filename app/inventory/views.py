from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.utils import timezone
from django.db.models import Count, Q

from .models import StockItem, StockUnit, StockAssignment


def _check_perm(request, codename):
    return request.user.has_perm(f"inventory.{codename}")


def _require_perm(request, codename):
    if not _check_perm(request, codename):
        return render(request, "inventory/access_denied.html", status=403)
    return None


# ------------------------------------------------------------------ #
# Stock overview                                                       #
# ------------------------------------------------------------------ #

@login_required
def stock_overview(request):
    deny = _require_perm(request, "view_stock")
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

    can_manage = _check_perm(request, "manage_stock")
    can_assign  = _check_perm(request, "assign_stock")

    ctx = {
        "items": items,
        "can_manage": can_manage,
        "can_assign": can_assign,
    }
    return render(request, "inventory/overview.html", ctx)


# ------------------------------------------------------------------ #
# Item detail + unit list                                              #
# ------------------------------------------------------------------ #

@login_required
def item_detail(request, item_pk):
    deny = _require_perm(request, "view_stock")
    if deny:
        return deny

    item  = get_object_or_404(StockItem, pk=item_pk)
    units = (
        item.units
        .exclude(status=StockUnit.Status.RETIRED)
        .select_related("current_holder", "added_by")
        .order_by("status", "-added_at")
    )

    can_manage = _check_perm(request, "manage_stock")
    can_assign  = _check_perm(request, "assign_stock")

    ctx = {
        "item": item,
        "units": units,
        "can_manage": can_manage,
        "can_assign": can_assign,
        "Status": StockUnit.Status,
    }
    return render(request, "inventory/item_detail.html", ctx)


# ------------------------------------------------------------------ #
# Quick-assign form                                                    #
# ------------------------------------------------------------------ #

@login_required
@require_POST
def assign_unit(request, unit_pk):
    deny = _require_perm(request, "assign_stock")
    if deny:
        return deny

    unit   = get_object_or_404(StockUnit, pk=unit_pk)
    action = request.POST.get("action", "")

    if action == "assign":
        recipient_name = request.POST.get("recipient_name", "").strip()
        notes          = request.POST.get("notes", "").strip()
        if not recipient_name:
            messages.error(request, "Recipient name is required.")
            return redirect("inventory:item_detail", item_pk=unit.item_id)

        from app.unifieduser.models import OrgPlayer
        recipient = OrgPlayer.objects.filter(
            Q(displaynamesearchcache__display_name__iexact=recipient_name) |
            Q(username__iexact=recipient_name)
        ).first()

        unit.status         = StockUnit.Status.ASSIGNED
        unit.current_holder = recipient
        unit.save(update_fields=["status", "current_holder", "updated_at"])

        StockAssignment.objects.create(
            unit=unit,
            action=StockAssignment.Action.ASSIGNED,
            recipient=recipient,
            recipient_name=recipient_name if not recipient else "",
            notes=notes,
            actioned_by=request.user,
        )
        messages.success(request, f"Assigned #{unit.pk} to {recipient_name}.")

    elif action == "return":
        notes = request.POST.get("notes", "").strip()
        prev_holder = unit.current_holder

        unit.status         = StockUnit.Status.AVAILABLE
        unit.current_holder = None
        unit.save(update_fields=["status", "current_holder", "updated_at"])

        StockAssignment.objects.create(
            unit=unit,
            action=StockAssignment.Action.RETURNED,
            recipient=prev_holder,
            notes=notes,
            actioned_by=request.user,
        )
        messages.success(request, f"Unit #{unit.pk} marked as returned.")

    elif action == "lost":
        notes = request.POST.get("notes", "").strip()
        prev_holder = unit.current_holder

        unit.status         = StockUnit.Status.LOST
        unit.current_holder = None
        unit.save(update_fields=["status", "current_holder", "updated_at"])

        StockAssignment.objects.create(
            unit=unit,
            action=StockAssignment.Action.LOST,
            recipient=prev_holder,
            notes=notes,
            actioned_by=request.user,
        )
        messages.success(request, f"Unit #{unit.pk} marked as lost.")

    else:
        messages.error(request, "Unknown action.")

    return redirect("inventory:item_detail", item_pk=unit.item_id)


# ------------------------------------------------------------------ #
# Assignment log                                                       #
# ------------------------------------------------------------------ #

@login_required
def assignment_log(request, item_pk=None):
    deny = _require_perm(request, "view_stock")
    if deny:
        return deny

    qs = (
        StockAssignment.objects
        .select_related("unit__item", "recipient", "actioned_by")
        .order_by("-actioned_at")
    )
    if item_pk:
        qs = qs.filter(unit__item_id=item_pk)

    item = get_object_or_404(StockItem, pk=item_pk) if item_pk else None

    ctx = {
        "log": qs[:200],
        "item": item,
    }
    return render(request, "inventory/assignment_log.html", ctx)
