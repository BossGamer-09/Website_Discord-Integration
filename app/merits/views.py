from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Q

from app.preferences.utils import get_global_preference
from .models import MeritRequest
from .prison_time import parse_prison_time
from .preferences import MeritFriendCheckUsername, MeritFriendCheckRSIUrl


def _check(request, codename):
    return request.user.has_perm(f"merits.{codename}")


def _require(request, codename):
    if not _check(request, codename):
        return render(request, "merits/access_denied.html", status=403)
    return None


# ------------------------------------------------------------------ #
# Request list
# ------------------------------------------------------------------ #

@login_required
def request_list(request):
    deny = _require(request, "view_merit_requests")
    if deny:
        return deny

    status_filter = request.GET.get("status", "")
    kind_filter   = request.GET.get("kind", "")

    qs = (
        MeritRequest.objects
        .select_related("requester", "reviewed_by", "linked_item")
        .order_by("status", "-submitted_at")
    )
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
        "can_review":    _check(request, "review_merit_request"),
        "can_fulfill":   _check(request, "fulfill_merit_request"),
    }
    return render(request, "merits/request_list.html", ctx)


# ------------------------------------------------------------------ #
# My requests (member view)
# ------------------------------------------------------------------ #

@login_required
def my_requests(request):
    deny = _require(request, "submit_merit_request")
    if deny:
        return deny

    qs = (
        MeritRequest.objects
        .filter(requester=request.user)
        .select_related("linked_item")
        .order_by("-submitted_at")
    )
    ctx = {
        "requests": qs,
        "Status":   MeritRequest.Status,
        "Kind":     MeritRequest.Kind,
    }
    return render(request, "merits/my_requests.html", ctx)


# ------------------------------------------------------------------ #
# Submit
# ------------------------------------------------------------------ #

@login_required
def friends_gate(request):
    """Interstitial: user must confirm they have the RSI contact on their friends list."""
    deny = _require(request, "submit_merit_request")
    if deny:
        return deny

    username = get_global_preference(MeritFriendCheckUsername.import_path) or ""
    rsi_url  = get_global_preference(MeritFriendCheckRSIUrl.import_path) or ""

    # Gate disabled — go straight to submit
    if not username:
        return redirect("merits:submit")

    # User confirmed in this session
    if request.session.get("merits_friends_confirmed"):
        return redirect("merits:submit")

    if request.method == "POST":
        if request.POST.get("confirmed") == "yes":
            request.session["merits_friends_confirmed"] = True
            return redirect("merits:submit")
        # "no" — stay on gate with warning
        return render(request, "merits/friends_gate.html", {
            "username": username,
            "rsi_url":  rsi_url,
            "denied":   True,
        })

    return render(request, "merits/friends_gate.html", {
        "username": username,
        "rsi_url":  rsi_url,
        "denied":   False,
    })


@login_required
def submit_request(request):
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
                # accept plain integer or prison-time string like "1h 30m"
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
            return render(request, "merits/submit.html", {
                "Kind": MeritRequest.Kind,
                "post": request.POST,
            })

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
            requester=request.user,
            kind=kind,
            title=title,
            reason=reason,
            amount=amount,
            rsi_handle=rsi_handle,
            during_event=during_event,
            purchase_for=purchase_for if kind == MeritRequest.Kind.MONEY else "",
        )
        messages.success(request, "Request submitted! Staff will review it shortly.")
        return redirect("merits:my_requests")

    return render(request, "merits/submit.html", {
        "Kind": MeritRequest.Kind,
    })


# ------------------------------------------------------------------ #
# Detail / staff actions
# ------------------------------------------------------------------ #

@login_required
def request_detail(request, pk):
    obj = get_object_or_404(MeritRequest, pk=pk)

    # Requester can see their own; staff with view perm can see all
    if obj.requester != request.user and not _check(request, "view_merit_requests"):
        return render(request, "merits/access_denied.html", status=403)

    ctx = {
        "obj":        obj,
        "Status":     MeritRequest.Status,
        "can_review": _check(request, "review_merit_request"),
    }
    return render(request, "merits/detail.html", ctx)


@login_required
@require_POST
def process_request(request, pk):
    deny = _require(request, "review_merit_request")
    if deny:
        return deny

    obj    = get_object_or_404(MeritRequest, pk=pk)
    action = request.POST.get("action", "")
    note   = request.POST.get("reviewer_note", "").strip()
    amount = request.POST.get("amount", "").strip()

    if action not in ("fulfill", "deny"):
        messages.error(request, "Unknown action.")
        return redirect("merits:detail", pk=pk)

    if amount:
        try:
            obj.amount = int(amount)
        except ValueError:
            messages.error(request, "Invalid amount.")
            return redirect("merits:detail", pk=pk)

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

    label = "fulfilled" if action == "fulfill" else "denied"
    messages.success(request, f"Request #{obj.pk} {label}.")
    return redirect("merits:detail", pk=pk)
