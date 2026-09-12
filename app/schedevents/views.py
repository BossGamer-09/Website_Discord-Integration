"""
app/schedevents/views.py

Web UI views for the scheduled events system.

  /events/                      dashboard       — upcoming event grid
  /events/manage/               manage          — staff management list (all statuses)
  /events/create/               event_create    — create form
  /events/<codename>/           event_detail    — detail + RSVP buttons
  /events/<codename>/edit/      event_edit      — edit form
  /events/<codename>/roster/    event_roster    — full RSVP roster
  /events/<codename>/rsvp/      event_rsvp_post — POST-only RSVP action
  /events/<codename>/cancel/    event_cancel    — POST-only cancel
  /events/<codename>/publish/   event_publish   — POST-only publish
"""
from datetime import timedelta, datetime as dt_datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from app.schedevents.models import EventIndicator, EventPlan, EventReminder, EventRSVP, EventTemplate, RSVPOption, EventAttendeeRole, EventRoleRestriction
from app.schedevents.permissions import (
    get_org_player,
    user_can_create_events,
    user_can_edit_event,
    user_can_force_checkin,
    user_can_manage_all_events,
    user_can_publish_events,
    user_can_view_events,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _access_denied(request, message=None):
    return render(request, "schedevents/access_denied.html", {"message": message}, status=403)


# ---------------------------------------------------------------------------
# Dashboard — upcoming events grid
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    if not user_can_view_events(request.user):
        return _access_denied(request, "You don't have permission to view Events.")

    now = timezone.now()
    org_user = get_org_player(request.user)
    tab = request.GET.get("tab", "upcoming")
    guild = _get_guild_id()

    # Per-tab filter params
    status_filter = request.GET.get("status", "")   # upcoming/past: ACTIVE|PUBLISHED|COMPLETED|CANCELLED
    rsvp_filter   = request.GET.get("rsvp", "")     # mine: GOING|MAYBE|WAITLISTED|NOT_GOING

    rsvp_map = {}

    if tab == "mine" and org_user:
        rsvp_qs = EventRSVP.objects.filter(user=org_user, event__guild_id=guild) \
            .select_related("event", "event__indicator")
        if rsvp_filter and rsvp_filter in {s.value for s in EventRSVP.RSVPStatus}:
            rsvp_qs = rsvp_qs.filter(status=rsvp_filter)
        rsvp_qs = list(rsvp_qs.order_by("event__planned_start_at"))
        events = [r.event for r in rsvp_qs]
        rsvp_map = {r.event_id: r for r in rsvp_qs}

    elif tab == "past":
        valid_past = {EventPlan.Status.COMPLETED, EventPlan.Status.CANCELLED}
        if status_filter in valid_past:
            status_in = [status_filter]
        else:
            status_in = list(valid_past)
        events = list(
            EventPlan.objects.filter(status__in=status_in, guild_id=guild)
            .select_related("indicator").order_by("-planned_start_at")[:150]
        )

    else:  # upcoming
        valid_upcoming = {EventPlan.Status.PUBLISHED, EventPlan.Status.ACTIVE}
        if status_filter in valid_upcoming:
            status_in = [status_filter]
        else:
            status_in = list(valid_upcoming)
        events = list(
            EventPlan.objects.filter(status__in=status_in, guild_id=guild)
            .select_related("indicator").order_by("planned_start_at")
        )

    # Annotate user RSVPs for non-mine tabs
    if tab != "mine" and org_user and events:
        event_ids = [p.codename_id for p in events]
        for rsvp in EventRSVP.objects.filter(event_id__in=event_ids, user=org_user):
            rsvp_map[rsvp.event_id] = rsvp

    annotated = [
        {
            "plan": plan,
            "user_rsvp": rsvp_map.get(plan.codename_id),
            "going_count": plan.going_count,
            "maybe_count": plan.maybe_count,
            "is_full": plan.is_full,
        }
        for plan in events
    ]

    grouped = _group_events(annotated, tab, now)

    # Build filter chip definitions for the template
    filter_chips = _build_filter_chips(tab, status_filter, rsvp_filter)

    return render(request, "schedevents/dashboard.html", {
        "grouped": grouped,
        "tab": tab,
        "status_filter": status_filter,
        "rsvp_filter": rsvp_filter,
        "filter_chips": filter_chips,
        "can_create": user_can_create_events(request.user),
        "can_manage": user_can_manage_all_events(request.user),
        "now": now,
        "total": len(annotated),
    })


def _build_filter_chips(tab, status_filter, rsvp_filter):
    """Return list of (label, url_params, is_active) for filter chips."""
    if tab == "past":
        active = status_filter or ""
        return [
            ("All",       f"tab=past",                    active == ""),
            ("Completed", f"tab=past&status=COMPLETED",   active == "COMPLETED"),
            ("Cancelled", f"tab=past&status=CANCELLED",   active == "CANCELLED"),
        ]
    if tab == "upcoming":
        active = status_filter or ""
        return [
            ("All",       f"tab=upcoming",                 active == ""),
            ("Live",      f"tab=upcoming&status=ACTIVE",   active == "ACTIVE"),
            ("Scheduled", f"tab=upcoming&status=PUBLISHED",active == "PUBLISHED"),
        ]
    if tab == "mine":
        active = rsvp_filter or ""
        return [
            ("All",        f"tab=mine",                       active == ""),
            ("Going",      f"tab=mine&rsvp=GOING",            active == "GOING"),
            ("Maybe",      f"tab=mine&rsvp=MAYBE",            active == "MAYBE"),
            ("Waitlisted", f"tab=mine&rsvp=WAITLISTED",       active == "WAITLISTED"),
            ("Not Going",  f"tab=mine&rsvp=NOT_GOING",        active == "NOT_GOING"),
        ]
    return []


def _group_events(annotated, tab, now):
    """Return list of (label, icon, entries) tuples for the dashboard."""
    from datetime import timedelta
    today = now.date()

    if tab == "past":
        from collections import OrderedDict
        buckets = OrderedDict()
        for entry in annotated:
            key = entry["plan"].planned_start_at.strftime("%B %Y")
            buckets.setdefault(key, []).append(entry)
        return [(k, "🗂", v) for k, v in buckets.items()]

    if tab == "mine":
        upcoming, past = [], []
        for entry in annotated:
            if entry["plan"].planned_start_at >= now or entry["plan"].status == EventPlan.Status.ACTIVE:
                upcoming.append(entry)
            else:
                past.append(entry)
        # Past mine sorted most-recent first
        past.sort(key=lambda e: e["plan"].planned_start_at, reverse=True)
        groups = []
        if upcoming:
            groups.append(("Upcoming", "📅", upcoming))
        if past:
            # Sub-group past mine by month
            from collections import OrderedDict
            buckets = OrderedDict()
            for entry in past:
                key = entry["plan"].planned_start_at.strftime("%B %Y")
                buckets.setdefault(key, []).append(entry)
            for k, v in buckets.items():
                groups.append((k, "🕐", v))
        return groups

    # Upcoming tab — group by time proximity
    buckets = [
        ("Live Now",   "🔴", []),
        ("Today",      "📅", []),
        ("Tomorrow",   "🌅", []),
        ("This Week",  "📆", []),
        ("Next Week",  "🗓", []),
        ("This Month", "📋", []),
        ("Later",      "🔮", []),
    ]
    week_end       = today + timedelta(days=7 - today.weekday())  # end of this ISO week
    next_week_end  = week_end + timedelta(days=7)
    month_end_day  = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    for entry in annotated:
        plan = entry["plan"]
        if plan.status == EventPlan.Status.ACTIVE:
            buckets[0][2].append(entry)
        else:
            d = plan.planned_start_at.date()
            if d == today:
                buckets[1][2].append(entry)
            elif d == today + timedelta(days=1):
                buckets[2][2].append(entry)
            elif d <= week_end:
                buckets[3][2].append(entry)
            elif d <= next_week_end:
                buckets[4][2].append(entry)
            elif d <= month_end_day:
                buckets[5][2].append(entry)
            else:
                buckets[6][2].append(entry)

    return [(label, icon, entries) for label, icon, entries in buckets if entries]


# ---------------------------------------------------------------------------
# Calendar view
# ---------------------------------------------------------------------------

@login_required
def event_calendar(request):
    if not user_can_view_events(request.user):
        return _access_denied(request, "You don't have permission to view Events.")

    import calendar as cal_mod
    from datetime import date

    now = timezone.now()
    try:
        year  = int(request.GET.get("year",  now.year))
        month = int(request.GET.get("month", now.month))
    except ValueError:
        year, month = now.year, now.month

    # Clamp to reasonable range
    year  = max(2020, min(2099, year))
    month = max(1,    min(12,   month))

    # Prev / next month links
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    # All days in the month
    _, num_days = cal_mod.monthrange(year, month)
    import datetime as _dt
    month_start = dt_datetime(year, month, 1, tzinfo=_dt.timezone.utc)
    month_end   = dt_datetime(year, month, num_days, 23, 59, 59, tzinfo=_dt.timezone.utc)

    # Fetch events that overlap this month
    events_qs = list(
        EventPlan.objects.filter(
            planned_start_at__gte=month_start,
            planned_start_at__lte=month_end,
            status__in=[
                EventPlan.Status.DRAFT,
                EventPlan.Status.PUBLISHED,
                EventPlan.Status.ACTIVE,
                EventPlan.Status.COMPLETED,
            ],
            guild_id=_get_guild_id(),
        ).select_related("indicator").order_by("planned_start_at")
    )

    # User's RSVPs for this month
    org_user = get_org_player(request.user)
    user_rsvps = {}
    if org_user and events_qs:
        event_ids = [e.codename_id for e in events_qs]
        for rsvp in EventRSVP.objects.filter(event_id__in=event_ids, user=org_user):
            user_rsvps[rsvp.event_id] = rsvp

    # Build event map: day_of_month → list of event dicts
    day_events: dict[int, list] = {}
    for plan in events_qs:
        local_day = plan.planned_start_at.day
        day_events.setdefault(local_day, []).append({
            "plan": plan,
            "user_rsvp": user_rsvps.get(plan.codename_id),
        })

    # Build calendar grid — list of weeks, each week is 7 day-dicts
    cal = cal_mod.Calendar(firstweekday=6)  # week starts Sunday
    weeks = []
    for week in cal.monthdatescalendar(year, month):
        row = []
        for d in week:
            row.append({
                "date": d,
                "in_month": d.month == month,
                "is_today": d == now.date(),
                "events": day_events.get(d.day, []) if d.month == month else [],
            })
        weeks.append(row)

    month_name = cal_mod.month_name[month]

    return render(request, "schedevents/calendar.html", {
        "weeks": weeks,
        "month_name": month_name,
        "year": year,
        "month": month,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        "today": now.date(),
        "can_create": user_can_create_events(request.user),
        "can_manage": user_can_manage_all_events(request.user),
    })


# ---------------------------------------------------------------------------
# Staff management view
# ---------------------------------------------------------------------------

@login_required
def event_manage(request):
    if not user_can_manage_all_events(request.user):
        return _access_denied(request, "You don't have permission to manage Events.")

    status_filter = request.GET.get("status", "")
    qs = EventPlan.objects.select_related("indicator", "created_by").order_by("-planned_start_at")
    if status_filter:
        qs = qs.filter(status=status_filter)

    return render(request, "schedevents/manage.html", {
        "events": qs[:200],
        "status_filter": status_filter,
        "status_choices": EventPlan.Status.choices,
        "can_create": user_can_create_events(request.user),
    })


# ---------------------------------------------------------------------------
# Event detail
# ---------------------------------------------------------------------------

def event_detail(request, codename_id):
    plan = get_object_or_404(
        EventPlan.objects.select_related("indicator", "vc_profile", "created_by"),
        codename_id=codename_id,
    )

    # Drafts are only visible to editors
    if plan.status == EventPlan.Status.DRAFT:
        if not request.user.is_authenticated or not user_can_edit_event(request.user, plan):
            raise Http404

    is_auth = request.user.is_authenticated
    org_user = get_org_player(request.user) if is_auth else None
    user_rsvp = None
    if org_user:
        user_rsvp = EventRSVP.objects.filter(event=plan, user=org_user).select_related("rsvp_option").first()

    # Custom RSVP options: per-event first, fall back to server-wide defaults
    rsvp_options = list(RSVPOption.objects.filter(
        event=plan, is_active=True
    ).order_by("sort_order"))
    if not rsvp_options:
        rsvp_options = list(RSVPOption.objects.filter(
            event__isnull=True, guild_id=plan.guild_id, is_active=True
        ).order_by("sort_order"))

    going_rsvps = EventRSVP.objects.filter(
        event=plan, status=EventRSVP.RSVPStatus.GOING, is_anonymous=False
    ).select_related("user")[:30] if is_auth else []
    maybe_rsvps = EventRSVP.objects.filter(
        event=plan, status=EventRSVP.RSVPStatus.MAYBE, is_anonymous=False
    ).select_related("user")[:15] if is_auth else []
    waitlist_rsvps = EventRSVP.objects.filter(
        event=plan, status=EventRSVP.RSVPStatus.WAITLISTED
    ).select_related("user")[:10] if is_auth else []

    rsvp_open = (
        is_auth
        and plan.status in (EventPlan.Status.PUBLISHED, EventPlan.Status.ACTIVE)
        and (plan.rsvp_deadline is None or timezone.now() < plan.rsvp_deadline)
    )

    return render(request, "schedevents/event_detail.html", {
        "plan": plan,
        "user_rsvp": user_rsvp,
        "going_rsvps": going_rsvps,
        "maybe_rsvps": maybe_rsvps,
        "waitlist_rsvps": waitlist_rsvps,
        "going_count": plan.going_count,
        "maybe_count": plan.maybe_count,
        "waitlist_count": plan.waitlist_count,
        "rsvp_open": rsvp_open,
        "rsvp_options": rsvp_options,
        "is_auth": is_auth,
        "can_edit": is_auth and user_can_edit_event(request.user, plan),
        "can_manage": is_auth and user_can_manage_all_events(request.user),
        "can_force_checkin": is_auth and user_can_force_checkin(request.user),
    })


# ---------------------------------------------------------------------------
# RSVP POST handler
# ---------------------------------------------------------------------------

@login_required
@require_POST
def event_rsvp_post(request, codename_id):
    plan = get_object_or_404(EventPlan, codename_id=codename_id)
    status = request.POST.get("status", "").upper()

    if status not in {s.value for s in EventRSVP.RSVPStatus}:
        messages.error(request, "Invalid RSVP status.")
        return redirect("schedevents:event_detail", codename_id=codename_id)

    if plan.status not in (EventPlan.Status.PUBLISHED, EventPlan.Status.ACTIVE):
        messages.error(request, "RSVPs are closed for this event.")
        return redirect("schedevents:event_detail", codename_id=codename_id)

    if plan.rsvp_deadline and timezone.now() > plan.rsvp_deadline:
        messages.error(request, "The RSVP deadline has passed.")
        return redirect("schedevents:event_detail", codename_id=codename_id)

    org_user = get_org_player(request.user)
    if not org_user:
        messages.error(request, "Your account isn't linked to a Blightveil profile.")
        return redirect("schedevents:event_detail", codename_id=codename_id)

    existing = EventRSVP.objects.filter(event=plan, user=org_user).first()

    # Toggle: same status → remove RSVP
    if existing and existing.status == status:
        existing.delete()
        messages.success(request, "Your RSVP has been removed.")
        return redirect("schedevents:event_detail", codename_id=codename_id)

    # Resolve effective status for GOING (capacity + waitlist)
    if status == EventRSVP.RSVPStatus.GOING:
        if plan.capacity is not None:
            current_going = EventRSVP.objects.filter(event=plan, status=EventRSVP.RSVPStatus.GOING).count()
            already_going = existing and existing.status == EventRSVP.RSVPStatus.GOING
            if not already_going and current_going >= plan.capacity:
                effective_status = EventRSVP.RSVPStatus.WAITLISTED if plan.waitlist_enabled else EventRSVP.RSVPStatus.NOT_GOING
            else:
                effective_status = EventRSVP.RSVPStatus.GOING
        else:
            effective_status = EventRSVP.RSVPStatus.GOING
    else:
        effective_status = status

    was_going = existing and existing.status == EventRSVP.RSVPStatus.GOING

    # Resolve optional custom RSVP option
    rsvp_option = None
    rsvp_option_id = request.POST.get("rsvp_option_id", "").strip()
    if rsvp_option_id:
        try:
            rsvp_option = RSVPOption.objects.get(pk=int(rsvp_option_id), is_active=True)
        except (RSVPOption.DoesNotExist, ValueError):
            pass

    if existing:
        existing.status = effective_status
        existing.rsvp_time = timezone.now()
        existing.rsvp_option = rsvp_option
        existing.save(update_fields=["status", "rsvp_time", "rsvp_option"])
    else:
        EventRSVP.objects.create(event=plan, user=org_user, status=effective_status, rsvp_option=rsvp_option)

    # Promote waitlist if GOING slot opened
    if was_going and status == EventRSVP.RSVPStatus.NOT_GOING:
        _promote_waitlist_sync(plan)

    status_msgs = {
        EventRSVP.RSVPStatus.GOING:      "✅ You're Going!",
        EventRSVP.RSVPStatus.MAYBE:      "❓ Marked as Maybe.",
        EventRSVP.RSVPStatus.NOT_GOING:  "❌ Marked as Not Going.",
        EventRSVP.RSVPStatus.WAITLISTED: "⏳ Added to the waitlist — you'll be promoted when a spot opens.",
        EventRSVP.RSVPStatus.PENDING:    "🕐 RSVP pending approval by event staff.",
    }
    messages.success(request, status_msgs.get(effective_status, "RSVP updated."))
    return redirect("schedevents:event_detail", codename_id=codename_id)


def _promote_waitlist_sync(plan: EventPlan):
    if plan.capacity is None:
        return
    current_going = EventRSVP.objects.filter(event=plan, status=EventRSVP.RSVPStatus.GOING).count()
    if current_going >= plan.capacity:
        return
    next_in_line = EventRSVP.objects.filter(
        event=plan, status=EventRSVP.RSVPStatus.WAITLISTED
    ).order_by("rsvp_time").first()
    if next_in_line:
        next_in_line.status = EventRSVP.RSVPStatus.GOING
        next_in_line.save(update_fields=["status"])


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@login_required
def event_create(request):
    if not user_can_create_events(request.user):
        return _access_denied(request, "You don't have permission to create Events.")

    indicators = EventIndicator.objects.all().order_by("id")
    from app.disfunction.models import VoiceChannelProfile
    from app.discordlogger.models import LoggedDiscordChannel
    from app.org.models import DiscordRole
    vc_profiles = VoiceChannelProfile.objects.filter(is_active=True).order_by("sort_order")
    text_channels = LoggedDiscordChannel.objects.filter(channel_type="text").order_by("name")
    discord_roles = list(DiscordRole.objects.order_by("discord_order"))
    guild_emojis = _get_guild_emojis()

    ctx_base = {
        "plan": None,
        "indicators": indicators,
        "template_categories_json_obj": _build_template_categories_json(),
        "vc_profiles": vc_profiles,
        "text_channels": text_channels,
        "discord_roles": discord_roles,
        "guild_emojis": guild_emojis,
        "can_publish": user_can_publish_events(request.user),
        "action": "create",
        "timezones": _common_timezones(),
        "reminder_choices": _reminder_choices(),
        "thread_archive_choices": _thread_archive_choices(),
        "selected_reminders": [],
        "selected_role_ids": [],
        "selected_mentions_on_create": [],
        "selected_mentions_on_start": [],
        "selected_mentions_on_vc_unlock": [],
        "mention_columns": _mention_columns([], [], []),
        "errors": {},
        "form_data": {},
    }

    if request.method == "POST":
        errors = {}
        data = request.POST

        title = data.get("title", "").strip()
        if not title:
            errors["title"] = "Title is required."

        indicator_id = data.get("indicator", "").strip()
        try:
            indicator = EventIndicator.objects.get(id=indicator_id)
        except EventIndicator.DoesNotExist:
            errors["indicator"] = "Select a valid indicator."
            indicator = None

        date_str = data.get("date", "").strip()
        time_str = data.get("time", "").strip()
        tz_name = _clean_timezone(data.get("timezone", "UTC"))
        start_dt = _parse_datetime(date_str, time_str, tz_name)
        if not start_dt:
            errors["date"] = "Invalid date or time format (use YYYY-MM-DD and HH:MM)."
        elif start_dt < timezone.now():
            errors["date"] = "Start time must be in the future."

        duration_str = data.get("duration", "2h").strip() or "2h"
        duration = _parse_duration(duration_str)
        if not duration:
            errors["duration"] = "Invalid duration (e.g. 2h, 90m, 1h30m)."

        capacity_str = data.get("capacity", "").strip()
        capacity = None
        if capacity_str:
            try:
                capacity = int(capacity_str)
                if capacity < 1:
                    raise ValueError
            except ValueError:
                errors["capacity"] = "Capacity must be a positive integer."

        if errors:
            sel_create = data.getlist("mentions_on_create")
            sel_start  = data.getlist("mentions_on_start")
            sel_vc     = data.getlist("mentions_on_vc_unlock")
            ctx_base.update({
                "errors": errors,
                "form_data": data,
                "selected_reminders": data.getlist("reminders"),
                "selected_mentions_on_create":    sel_create,
                "selected_mentions_on_start":     sel_start,
                "selected_mentions_on_vc_unlock": sel_vc,
                "mention_columns": _mention_columns(sel_create, sel_start, sel_vc),
            })
            return render(request, "schedevents/event_form.html", ctx_base)

        org_user = get_org_player(request.user)

        # Parse rsvp_deadline from combined datetime-local field
        rsvp_deadline = None
        deadline_raw = data.get("rsvp_deadline", "").strip()
        if deadline_raw:
            try:
                from datetime import datetime as dt_cls
                from zoneinfo import ZoneInfo
                naive = dt_cls.fromisoformat(deadline_raw)
                rsvp_deadline = naive.replace(tzinfo=ZoneInfo(tz_name))
            except Exception:
                pass

        channel_id = _clean_announcement_channel_id(data.get("announcement_channel_id", ""))

        archive_raw = data.get("thread_auto_archive_duration", "OneDay") or "OneDay"
        if archive_raw not in {c[0] for c in _thread_archive_choices()}:
            archive_raw = "OneDay"
        start_msg_raw = data.get("thread_start_message_type", "Thread") or "Thread"
        if start_msg_raw not in {"Thread", "Channel", "None"}:
            start_msg_raw = "Thread"

        plan = EventPlan.objects.create(
            title=_clean_text(title, 200),
            description=_clean_text(data.get("description", ""), 2000),
            location=_clean_text(data.get("location", ""), 200),
            indicator=indicator,
            created_by=org_user,
            planned_start_at=start_dt,
            planned_duration=duration,
            timezone_name=tz_name,
            guild_id=_get_guild_id(),
            capacity=capacity,
            waitlist_enabled=bool(data.get("waitlist_enabled")),
            auto_generate_vc=bool(data.get("auto_generate_vc")),
            private_thread=bool(data.get("private_thread")),
            vc_profile_id=_clean_vc_profile_id(data.get("vc_profile")),
            cover_image_url=_clean_url(data.get("cover_image_url", "")),
            color_hex=_clean_color(data.get("color_hex", "")),
            hide_attendees=bool(data.get("hide_attendees")),
            allow_maintain_rsvp=bool(data.get("allow_maintain_rsvp")),
            recurring_rule=_resolve_rrule(data) or None,
            rsvp_deadline=rsvp_deadline,
            announcement_channel_override_id=channel_id,
            thread_title_template=_clean_thread_title(data.get("thread_title_template", "$EventName")),
            thread_auto_archive_duration=archive_raw,
            thread_join_on_rsvp=bool(data.get("thread_join_on_rsvp")),
            thread_start_message_type=start_msg_raw,
            emoji_going=_clean_emoji(data.get("emoji_going", ""), "✅"),
            emoji_maybe=_clean_emoji(data.get("emoji_maybe", ""), "❓"),
            emoji_not_going=_clean_emoji(data.get("emoji_not_going", ""), "❌"),
            mentions_on_create=_parse_role_ids(data.getlist("mentions_on_create")),
            mentions_on_start=_parse_role_ids(data.getlist("mentions_on_start")),
            mentions_on_vc_unlock=_parse_role_ids(data.getlist("mentions_on_vc_unlock")),
        )

        if org_user:
            plan.organizers.add(org_user)

        # Reminders from checkboxes (values are minute integers)
        for mins_str in data.getlist("reminders"):
            try:
                EventReminder.objects.get_or_create(
                    event=plan, minutes_before=int(mins_str), target="DM"
                )
            except (ValueError, TypeError):
                pass

        # Role restrictions — which Discord roles may RSVP
        for role_id_str in data.getlist("eligible_roles"):
            try:
                EventRoleRestriction.objects.get_or_create(
                    event=plan, role_id=int(role_id_str), rsvp_option=None
                )
            except (ValueError, TypeError):
                pass

        if data.get("save_as_template"):
            tpl_name = data.get("template_name", "").strip() or plan.title
            EventTemplate.objects.create(
                name=tpl_name,
                indicator=plan.indicator,
                default_duration=plan.planned_duration,
                default_description=plan.description,
                auto_generate_vc=plan.auto_generate_vc,
                private_thread=plan.private_thread,
                vc_profile=plan.vc_profile,
                default_capacity=plan.capacity,
                cover_image_url=plan.cover_image_url,
                color_hex=plan.color_hex,
                hide_attendees=plan.hide_attendees,
                allow_maintain_rsvp=plan.allow_maintain_rsvp,
                recurring_rule=plan.recurring_rule,
                thread_title_template=plan.thread_title_template,
                thread_auto_archive_duration=plan.thread_auto_archive_duration,
                thread_join_on_rsvp=plan.thread_join_on_rsvp,
                thread_start_message_type=plan.thread_start_message_type,
                emoji_going=plan.emoji_going,
                emoji_maybe=plan.emoji_maybe,
                emoji_not_going=plan.emoji_not_going,
                mentions_on_create=plan.mentions_on_create,
                mentions_on_start=plan.mentions_on_start,
                mentions_on_vc_unlock=plan.mentions_on_vc_unlock,
                default_reminders_minutes=[r.minutes_before for r in plan.reminders.all()],
                created_by=request.user,
            )
            messages.success(request, f"Template '{tpl_name}' saved.")

        messages.success(request, f"Event '{plan.title}' created as a Draft. Publish it to announce to members.")

        if user_can_publish_events(request.user) and data.get("auto_publish"):
            plan.status = EventPlan.Status.PUBLISHED
            plan.save(update_fields=["status"])
            messages.success(request, "Event published!")

        return redirect("schedevents:event_detail", codename_id=plan.codename_id)

    return render(request, "schedevents/event_form.html", ctx_base)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@login_required
def event_edit(request, codename_id):
    plan = get_object_or_404(
        EventPlan.objects.select_related("indicator", "vc_profile"),
        codename_id=codename_id,
    )
    if not user_can_edit_event(request.user, plan):
        return _access_denied(request, "You don't have permission to edit this event.")

    if plan.status in (EventPlan.Status.COMPLETED, EventPlan.Status.CANCELLED):
        messages.error(request, "Cannot edit a completed or cancelled event.")
        return redirect("schedevents:event_detail", codename_id=codename_id)

    indicators = EventIndicator.objects.all().order_by("id")
    from app.disfunction.models import VoiceChannelProfile
    from app.discordlogger.models import LoggedDiscordChannel
    from app.org.models import DiscordRole
    vc_profiles = VoiceChannelProfile.objects.filter(is_active=True).order_by("sort_order")
    text_channels = LoggedDiscordChannel.objects.filter(channel_type="text").order_by("name")
    discord_roles = list(DiscordRole.objects.order_by("discord_order"))
    guild_emojis = _get_guild_emojis()
    existing_reminders = list(plan.reminders.all())
    selected_reminders = [str(r.minutes_before) for r in existing_reminders]
    selected_role_ids = list(
        plan.role_restrictions.filter(rsvp_option__isnull=True).values_list("role_id", flat=True)
    )

    # Pre-populate form_data from the plan so all fields render correctly on GET
    from zoneinfo import ZoneInfo
    _plan_tz = ZoneInfo(plan.timezone_name or "UTC")
    _local_start = plan.planned_start_at.astimezone(_plan_tz) if plan.planned_start_at else None
    _dur = plan.planned_duration
    if _dur:
        _h = int(_dur.total_seconds()) // 3600
        _m = (int(_dur.total_seconds()) % 3600) // 60
        _dur_str = (f"{_h}h{_m}m" if _h and _m else f"{_h}h" if _h else f"{_m}m")
    else:
        _dur_str = ""
    _deadline = None
    if plan.rsvp_deadline:
        _deadline = plan.rsvp_deadline.astimezone(_plan_tz).strftime("%Y-%m-%dT%H:%M")
    _preset = ""
    if plan.recurring_rule:
        _preset = plan.recurring_rule if plan.recurring_rule in (
            "FREQ=DAILY", "FREQ=WEEKLY", "FREQ=WEEKLY;BYDAY=MO,WE,FR",
            "FREQ=BIWEEKLY", "FREQ=MONTHLY",
        ) else "custom"

    _prefill = {
        "date": _local_start.strftime("%Y-%m-%d") if _local_start else "",
        "time": _local_start.strftime("%H:%M") if _local_start else "",
        "timezone": plan.timezone_name or "UTC",
        "duration": _dur_str,
        "rsvp_deadline": _deadline or "",
        "recurring_preset": _preset,
        "announcement_channel_id": str(plan.announcement_channel_override_id) if plan.announcement_channel_override_id else "",
        "mentions_on_create": [str(r) for r in (plan.mentions_on_create or [])],
        "mentions_on_start": [str(r) for r in (plan.mentions_on_start or [])],
        "mentions_on_vc_unlock": [str(r) for r in (plan.mentions_on_vc_unlock or [])],
    }

    ctx_base = {
        "plan": plan,
        "indicators": indicators,
        "template_categories_json_obj": _build_template_categories_json(),
        "vc_profiles": vc_profiles,
        "text_channels": text_channels,
        "discord_roles": discord_roles,
        "guild_emojis": guild_emojis,
        "can_publish": user_can_publish_events(request.user),
        "action": "edit",
        "timezones": _common_timezones(),
        "reminder_choices": _reminder_choices(),
        "thread_archive_choices": _thread_archive_choices(),
        "selected_reminders": selected_reminders,
        "selected_role_ids": selected_role_ids,
        "selected_mentions_on_create":    [str(r) for r in (plan.mentions_on_create or [])],
        "selected_mentions_on_start":     [str(r) for r in (plan.mentions_on_start or [])],
        "selected_mentions_on_vc_unlock": [str(r) for r in (plan.mentions_on_vc_unlock or [])],
        "mention_columns": _mention_columns(
            plan.mentions_on_create or [],
            plan.mentions_on_start or [],
            plan.mentions_on_vc_unlock or [],
        ),
        "errors": {},
        "form_data": _prefill,
    }

    if request.method == "POST":
        errors = {}
        data = request.POST

        title = data.get("title", "").strip()
        if not title:
            errors["title"] = "Title is required."

        date_str = data.get("date", "").strip()
        time_str = data.get("time", "").strip()
        tz_name = _clean_timezone(data.get("timezone", plan.timezone_name))
        start_dt = _parse_datetime(date_str, time_str, tz_name)
        if not start_dt:
            errors["date"] = "Invalid date or time."

        duration_str = data.get("duration", "2h").strip() or "2h"
        duration = _parse_duration(duration_str)
        if not duration:
            errors["duration"] = "Invalid duration."

        capacity_str = data.get("capacity", "").strip()
        capacity = None
        if capacity_str:
            try:
                capacity = int(capacity_str)
            except ValueError:
                errors["capacity"] = "Must be a number."

        if errors:
            sel_create = data.getlist("mentions_on_create")
            sel_start  = data.getlist("mentions_on_start")
            sel_vc     = data.getlist("mentions_on_vc_unlock")
            ctx_base.update({
                "errors": errors,
                "form_data": data,
                "selected_reminders": data.getlist("reminders"),
                "selected_mentions_on_create":    sel_create,
                "selected_mentions_on_start":     sel_start,
                "selected_mentions_on_vc_unlock": sel_vc,
                "mention_columns": _mention_columns(sel_create, sel_start, sel_vc),
            })
            return render(request, "schedevents/event_form.html", ctx_base)

        try:
            indicator = EventIndicator.objects.get(id=data.get("indicator"))
        except EventIndicator.DoesNotExist:
            indicator = plan.indicator

        channel_id = _clean_announcement_channel_id(data.get("announcement_channel_id", ""))

        archive_raw = data.get("thread_auto_archive_duration", "OneDay") or "OneDay"
        if archive_raw not in {c[0] for c in _thread_archive_choices()}:
            archive_raw = "OneDay"
        start_msg_raw = data.get("thread_start_message_type", "Thread") or "Thread"
        if start_msg_raw not in {"Thread", "Channel", "None"}:
            start_msg_raw = "Thread"

        plan.title = _clean_text(title, 200)
        plan.description = _clean_text(data.get("description", ""), 2000)
        plan.location = _clean_text(data.get("location", ""), 200)
        plan.indicator = indicator
        plan.planned_start_at = start_dt
        plan.planned_duration = duration
        plan.timezone_name = tz_name
        plan.capacity = capacity
        plan.waitlist_enabled = bool(data.get("waitlist_enabled"))
        plan.auto_generate_vc = bool(data.get("auto_generate_vc"))
        plan.private_thread = bool(data.get("private_thread"))
        plan.vc_profile_id = _clean_vc_profile_id(data.get("vc_profile"))
        plan.cover_image_url = _clean_url(data.get("cover_image_url", ""))
        plan.color_hex = _clean_color(data.get("color_hex", ""))
        plan.hide_attendees = bool(data.get("hide_attendees"))
        plan.allow_maintain_rsvp = bool(data.get("allow_maintain_rsvp"))
        plan.recurring_rule = _resolve_rrule(data) or None
        plan.announcement_channel_override_id = channel_id
        plan.thread_title_template = _clean_thread_title(data.get("thread_title_template", "$EventName"))
        plan.thread_auto_archive_duration = archive_raw
        plan.thread_join_on_rsvp = bool(data.get("thread_join_on_rsvp"))
        plan.thread_start_message_type = start_msg_raw
        plan.emoji_going     = _clean_emoji(data.get("emoji_going", ""), "✅")
        plan.emoji_maybe     = _clean_emoji(data.get("emoji_maybe", ""), "❓")
        plan.emoji_not_going = _clean_emoji(data.get("emoji_not_going", ""), "❌")
        plan.mentions_on_create    = _parse_role_ids(data.getlist("mentions_on_create"))
        plan.mentions_on_start     = _parse_role_ids(data.getlist("mentions_on_start"))
        plan.mentions_on_vc_unlock = _parse_role_ids(data.getlist("mentions_on_vc_unlock"))
        plan.save()

        # Sync reminders: replace with submitted set
        plan.reminders.all().delete()
        for mins_str in data.getlist("reminders"):
            try:
                EventReminder.objects.create(event=plan, minutes_before=int(mins_str), target="DM")
            except (ValueError, TypeError):
                pass

        # Sync role restrictions (global, no rsvp_option)
        plan.role_restrictions.filter(rsvp_option__isnull=True).delete()
        for role_id_str in data.getlist("eligible_roles"):
            try:
                EventRoleRestriction.objects.create(event=plan, role_id=int(role_id_str), rsvp_option=None)
            except (ValueError, TypeError):
                pass

        if data.get("save_as_template"):
            tpl_name = data.get("template_name", "").strip() or plan.title
            EventTemplate.objects.create(
                name=tpl_name,
                indicator=plan.indicator,
                default_duration=plan.planned_duration,
                default_description=plan.description,
                auto_generate_vc=plan.auto_generate_vc,
                private_thread=plan.private_thread,
                vc_profile=plan.vc_profile,
                default_capacity=plan.capacity,
                cover_image_url=plan.cover_image_url,
                color_hex=plan.color_hex,
                hide_attendees=plan.hide_attendees,
                allow_maintain_rsvp=plan.allow_maintain_rsvp,
                recurring_rule=plan.recurring_rule,
                thread_title_template=plan.thread_title_template,
                thread_auto_archive_duration=plan.thread_auto_archive_duration,
                thread_join_on_rsvp=plan.thread_join_on_rsvp,
                thread_start_message_type=plan.thread_start_message_type,
                emoji_going=plan.emoji_going,
                emoji_maybe=plan.emoji_maybe,
                emoji_not_going=plan.emoji_not_going,
                mentions_on_create=plan.mentions_on_create,
                mentions_on_start=plan.mentions_on_start,
                mentions_on_vc_unlock=plan.mentions_on_vc_unlock,
                default_reminders_minutes=[r.minutes_before for r in plan.reminders.all()],
                created_by=request.user,
            )
            messages.success(request, f"Template '{tpl_name}' saved.")

        messages.success(request, "Event updated.")
        return redirect("schedevents:event_detail", codename_id=plan.codename_id)

    return render(request, "schedevents/event_form.html", ctx_base)


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

@login_required
def event_roster(request, codename_id):
    plan = get_object_or_404(EventPlan.objects.select_related("indicator"), codename_id=codename_id)

    if plan.status == EventPlan.Status.DRAFT and not user_can_edit_event(request.user, plan):
        raise Http404

    rsvps = list(EventRSVP.objects.filter(event=plan).select_related("user", "rsvp_option").order_by("rsvp_time"))

    # RSVPOptions: per-event overrides first, then guild-wide defaults
    from django.db.models import Q
    rsvp_options = list(RSVPOption.objects.filter(event=plan, is_active=True).order_by("sort_order"))
    if not rsvp_options:
        rsvp_options = list(RSVPOption.objects.filter(
            event__isnull=True, guild_id=plan.guild_id, is_active=True
        ).order_by("sort_order"))

    if rsvp_options:
        option_pks = {o.pk for o in rsvp_options}
        buckets: dict = {o.pk: [] for o in rsvp_options}
        not_going: list = []
        for rsvp in rsvps:
            if rsvp.rsvp_option_id and rsvp.rsvp_option_id in option_pks:
                buckets[rsvp.rsvp_option_id].append(rsvp)
            elif rsvp.status == EventRSVP.RSVPStatus.NOT_GOING:
                not_going.append(rsvp)
            # silently drop unmatched non-not-going (shouldn't exist in normal flow)
        # Build list of (option, rsvps) tuples — iterable in templates without dict lookup
        option_sections = [(opt, buckets[opt.pk]) for opt in rsvp_options]
        going = maybe = waitlisted = []
    else:
        option_sections = []
        going, maybe, waitlisted, not_going = [], [], [], []
        for rsvp in rsvps:
            if rsvp.status == EventRSVP.RSVPStatus.GOING:
                going.append(rsvp)
            elif rsvp.status == EventRSVP.RSVPStatus.MAYBE:
                maybe.append(rsvp)
            elif rsvp.status == EventRSVP.RSVPStatus.WAITLISTED:
                waitlisted.append(rsvp)
            elif rsvp.status == EventRSVP.RSVPStatus.NOT_GOING:
                not_going.append(rsvp)

    org_user = get_org_player(request.user)
    user_rsvp = EventRSVP.objects.filter(event=plan, user=org_user).first() if org_user else None

    return render(request, "schedevents/event_roster.html", {
        "plan": plan,
        "option_sections": option_sections,   # [(RSVPOption, [EventRSVP, …]), …]
        "going": going,
        "maybe": maybe,
        "waitlisted": waitlisted,
        "not_going": not_going,
        "total": len(rsvps),
        "can_checkin": user_can_force_checkin(request.user),
        "can_manage": user_can_manage_all_events(request.user),
        "user_rsvp": user_rsvp,
    })


# ---------------------------------------------------------------------------
# POST-only actions
# ---------------------------------------------------------------------------

@login_required
@require_POST
def event_cancel(request, codename_id):
    plan = get_object_or_404(EventPlan, codename_id=codename_id)
    if not user_can_edit_event(request.user, plan):
        return _access_denied(request, "You don't have permission to cancel this event.")
    if plan.status in (EventPlan.Status.COMPLETED, EventPlan.Status.CANCELLED):
        messages.error(request, "Event is already completed or cancelled.")
        return redirect("schedevents:event_detail", codename_id=codename_id)
    plan.status = EventPlan.Status.CANCELLED
    plan.save(update_fields=["status"])
    messages.success(request, f"Event '{plan.title}' has been cancelled.")
    return redirect("schedevents:event_detail", codename_id=codename_id)


@login_required
@require_POST
def event_publish(request, codename_id):
    plan = get_object_or_404(EventPlan, codename_id=codename_id)
    if not user_can_publish_events(request.user):
        return _access_denied(request, "You don't have permission to publish Events.")
    if plan.status != EventPlan.Status.DRAFT:
        messages.error(request, f"Event must be a draft to publish (currently {plan.get_status_display()}).")
        return redirect("schedevents:event_detail", codename_id=codename_id)
    plan.status = EventPlan.Status.PUBLISHED
    plan.save(update_fields=["status"])
    messages.success(request, f"Event '{plan.title}' published! The bot will post the announcement shortly.")
    return redirect("schedevents:event_detail", codename_id=codename_id)


@login_required
@require_POST
def event_force_checkin(request, codename_id):
    if not user_can_force_checkin(request.user):
        return _access_denied(request, "You don't have permission to force check-in members.")
    plan = get_object_or_404(EventPlan, codename_id=codename_id)
    rsvp_id = request.POST.get("rsvp_id")
    rsvp = get_object_or_404(EventRSVP, pk=rsvp_id, event=plan)
    rsvp.checked_in = True
    rsvp.check_in_time = timezone.now()
    rsvp.check_in_source = EventRSVP.CheckInSource.MANUAL
    rsvp.save(update_fields=["checked_in", "check_in_time", "check_in_source"])
    messages.success(request, "Check-in recorded.")
    return redirect("schedevents:event_roster", codename_id=codename_id)


# ---------------------------------------------------------------------------
# Sesh → EventTemplate import (web UI)
# ---------------------------------------------------------------------------

@login_required
def event_import_template(request):
    """
    Paste a Sesh.fyi event info dump and create an EventTemplate from it.

    GET  → show the paste form
    POST → parse + preview (step 1) or confirm create (step 2)
    """
    if not user_can_create_events(request.user):
        return _access_denied(request, "You don't have permission to import Event templates.")

    from app.schedevents.sesh_import import parse_sesh_dump

    # Step 2 — confirm create (hidden field confirm=1 is present)
    if request.method == "POST" and request.POST.get("confirm") == "1":
        import json as _json
        raw = request.POST.get("raw_dump", "")
        parsed = parse_sesh_dump(raw)

        indicator_id = request.POST.get("indicator", "").strip()
        try:
            indicator = EventIndicator.objects.get(id=indicator_id)
        except EventIndicator.DoesNotExist:
            indicator = EventIndicator.objects.order_by("id").first()

        category_id = request.POST.get("category", "").strip() or None
        from app.schedevents.models import TemplateCategory
        category = None
        if category_id:
            try:
                category = TemplateCategory.objects.get(pk=int(category_id))
            except (TemplateCategory.DoesNotExist, ValueError):
                pass

        org_user = get_org_player(request.user)
        dur = parsed.get("planned_duration") or timedelta(hours=2)

        tpl = EventTemplate.objects.create(
            name=parsed["title"],
            default_description=parsed["description"],
            indicator=indicator,
            category=category,
            default_duration=dur,
            cover_image_url=parsed.get("cover_image_url"),
            color_hex=parsed.get("color_hex"),
            hide_attendees=parsed.get("hide_attendees", False),
            allow_maintain_rsvp=parsed.get("allow_maintain_rsvp", False),
            recurring_rule=parsed.get("recurring_rule"),
            thread_title_template=parsed.get("thread_title_template") or "$EventName",
            thread_auto_archive_duration=parsed.get("thread_auto_archive_duration") or "OneDay",
            thread_join_on_rsvp=parsed.get("thread_join_on_rsvp", True),
            thread_start_message_type=parsed.get("thread_start_message_type") or "Thread",
            default_reminders_minutes=[
                s["minutes_before"] for s in (parsed.get("notification_specs") or [])
                if s.get("minutes_before") is not None
            ],
            created_by=org_user,
            is_active=True,
        )

        # Server-wide RSVP options from the dump
        guild_id = _get_guild_id()
        for i, spec in enumerate(parsed.get("rsvp_option_specs") or []):
            RSVPOption.objects.create(
                guild_id=guild_id,
                event=None,  # server-wide default
                emoji=spec["emoji"],
                label=spec["label"],
                sort_order=i,
            )

        messages.success(
            request,
            f"Template '{tpl.name}' created."
            + (" Role IDs for mentions/restrictions still need to be set in admin." if parsed.get("mentions_on_create") or parsed.get("role_restriction_names") else ""),
        )
        return HttpResponseRedirect(f"/admin/schedevents/eventtemplate/{tpl.pk}/change/")

    # Step 1 — parse and preview
    if request.method == "POST":
        raw = request.POST.get("raw_dump", "").strip()
        if not raw:
            return render(request, "schedevents/import_template.html", {
                "error": "Paste a Sesh event dump first.",
                "indicators": EventIndicator.objects.all().order_by("id"),
                "categories": _template_categories_flat(),
            })

        parsed = parse_sesh_dump(raw)
        indicators = EventIndicator.objects.all().order_by("id")
        return render(request, "schedevents/import_template.html", {
            "step": "preview",
            "parsed": parsed,
            "raw_dump": raw,
            "indicators": indicators,
            "categories": _template_categories_flat(),
            "default_indicator": indicators.first(),
        })

    # GET — blank form
    return render(request, "schedevents/import_template.html", {
        "indicators": EventIndicator.objects.all().order_by("id"),
        "categories": _template_categories_flat(),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _common_timezones():
    """Short curated list of common timezones for the form selector."""
    return [
        "UTC",
        "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
        "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
        "America/Vancouver", "America/Toronto", "America/Sao_Paulo",
        "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
        "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo",
        "Australia/Sydney", "Pacific/Auckland",
    ]


def _reminder_choices():
    """(minutes_before, display_label) pairs for reminder checkboxes."""
    return [
        (1440, "24h before"),
        (120, "2h before"),
        (60, "1h before"),
        (30, "30m before"),
        (15, "15m before"),
        (5, "5m before"),
    ]


def _thread_archive_choices():
    return EventPlan.ThreadArchiveDuration.choices


def _clean_color(value: str) -> str | None:
    """Return a valid #RRGGBB hex string or None."""
    import re
    v = (value or "").strip()
    if re.fullmatch(r'#[0-9a-fA-F]{6}', v):
        return v
    return None


# --- Input sanitizers -----------------------------------------------------

import re as _re

# Broad Unicode emoji pick (covers BMP symbols, pictographs, flags, ZWJ sequences)
_EMOJI_UNICODE_RE = _re.compile(
    r'^[\U0001F000-\U0001FFFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF'
    r'\u200d\ufe0f\u20e3\U000E0020-\U000E007F]{1,10}$'
)
_EMOJI_CUSTOM_RE = _re.compile(r'^<a?:[A-Za-z0-9_]{2,32}:\d{15,22}>$')


def _clean_emoji(value: str, fallback: str) -> str:
    """Accept Unicode emoji or <a?:name:id>, else fallback."""
    v = (value or "").strip()
    if not v:
        return fallback
    if len(v) > 64:
        return fallback
    if _EMOJI_CUSTOM_RE.fullmatch(v):
        return v
    if _EMOJI_UNICODE_RE.fullmatch(v):
        return v
    return fallback


def _clean_url(value: str) -> str | None:
    """Allow only http/https URLs; strip anything else."""
    v = (value or "").strip()
    if not v:
        return None
    if len(v) > 512:
        return None
    low = v.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return None
    # No control chars / whitespace in URL
    if _re.search(r'[\s<>"\']', v):
        return None
    return v


def _clean_text(value: str, max_len: int) -> str:
    """Trim + strip NULs and collapse exotic whitespace. Django auto-escapes on render."""
    v = (value or "").replace("\x00", "").strip()
    if len(v) > max_len:
        v = v[:max_len]
    return v


def _clean_thread_title(value: str) -> str:
    """Discord channel/thread names can't contain @ # mentions and are capped at 100."""
    v = (value or "").replace("\x00", "").strip()
    v = v.replace("@everyone", "").replace("@here", "")
    if len(v) > 100:
        v = v[:100]
    return v or "$EventName"


def _clean_timezone(value: str) -> str:
    """Validate tz via zoneinfo; fall back to UTC."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    v = (value or "UTC").strip()
    try:
        ZoneInfo(v)
        return v
    except (ZoneInfoNotFoundError, Exception):
        return "UTC"


def _clean_vc_profile_id(value) -> int | None:
    """Allow only an int pk that maps to an existing active VoiceChannelProfile."""
    from app.disfunction.models import VoiceChannelProfile
    try:
        pk = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if VoiceChannelProfile.objects.filter(pk=pk, is_active=True).exists():
        return pk
    return None


def _clean_announcement_channel_id(value) -> int | None:
    """Only accept channel IDs the bot already tracks as text channels."""
    from app.discordlogger.models import LoggedDiscordChannel
    v = (value or "").strip()
    if not v.isdigit():
        return None
    try:
        cid = int(v)
    except (TypeError, ValueError):
        return None
    if LoggedDiscordChannel.objects.filter(pk=cid, channel_type="text").exists():
        return cid
    return None


def _parse_role_ids(values) -> list[int]:
    """Convert a list of form-submitted role ID strings → deduped list of ints."""
    out: list[int] = []
    seen: set[int] = set()
    for raw in values or []:
        try:
            rid = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if rid and rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def _mention_columns(sel_create, sel_start, sel_vc) -> list[dict]:
    """Column definitions for the Ping Roles picker in event_form.html."""
    return [
        {"field": "mentions_on_create",    "label": "On Publish",   "selected": [str(r) for r in sel_create]},
        {"field": "mentions_on_start",     "label": "On Start",     "selected": [str(r) for r in sel_start]},
        {"field": "mentions_on_vc_unlock", "label": "On VC Unlock", "selected": [str(r) for r in sel_vc]},
    ]


def _resolve_rrule(data) -> str:
    """Return RRULE string from form data (preset or custom field)."""
    preset = data.get("recurring_preset", "").strip()
    if preset == "custom":
        return data.get("recurring_rule", "").strip()
    return preset if preset else ""


def _template_categories_flat():
    from app.schedevents.models import TemplateCategory
    return list(TemplateCategory.objects.order_by("sort_order", "name"))


def _build_template_categories_json():
    """Build the object consumed by the template-picker JS on event_form.html.
    Returned as a plain list/dict so callers can run it through `json_script` (XSS-safe)."""
    templates_qs = EventTemplate.objects.filter(is_active=True).select_related("category", "indicator").order_by("name")
    categories_map: dict = {}
    uncategorised: list = []
    for tpl in templates_qs:
        cat_name = tpl.category.name if tpl.category else None
        entry = {
            "id": tpl.pk, "name": tpl.name, "description": tpl.description,
            "indicator_id": tpl.indicator_id,
            "default_duration_seconds": int(tpl.default_duration.total_seconds()),
            "default_description": tpl.default_description,
            "default_capacity": tpl.default_capacity,
            "auto_generate_vc": tpl.auto_generate_vc,
            "private_thread": tpl.private_thread,
            "cover_image_url": tpl.cover_image_url or "",
            "color_hex": tpl.color_hex or "",
            "hide_attendees": tpl.hide_attendees,
            "allow_maintain_rsvp": tpl.allow_maintain_rsvp,
            "thread_title_template": tpl.thread_title_template,
            "thread_auto_archive_duration": tpl.thread_auto_archive_duration,
            "thread_join_on_rsvp": tpl.thread_join_on_rsvp,
            "vc_profile_id": tpl.vc_profile_id,
            "emoji_going": tpl.emoji_going or "✅",
            "emoji_maybe": tpl.emoji_maybe or "❓",
            "emoji_not_going": tpl.emoji_not_going or "❌",
            "location": getattr(tpl, "location", "") or "",
        }
        if cat_name:
            categories_map.setdefault(cat_name, {"sort": tpl.category.sort_order, "templates": []})["templates"].append(entry)
        else:
            uncategorised.append(entry)
    # Convert (name, {sort, templates}) tuples → [name, {templates}] pairs for JSON-safe output.
    template_categories = [
        [name, {"templates": payload["templates"]}]
        for name, payload in sorted(categories_map.items(), key=lambda x: x[1]["sort"])
    ]
    if uncategorised:
        template_categories.append(["Other", {"templates": uncategorised}])
    return template_categories


def _get_guild_id() -> int:
    """Read guild ID from preferences at call time (avoids import-time DB access)."""
    try:
        from app.preferences.utils import get_global_preference
        from app.discordauth.preferences import BotGuildID
        gid = get_global_preference(BotGuildID.import_path)
        return int(gid) if gid else 0
    except Exception:
        return 0


def _get_guild_emojis() -> list[dict]:
    """
    Fetch custom emojis from the configured guild via Discord REST API.
    Cached for 5 minutes. Returns [{id, name, animated, token, url}, …].
    """
    from django.core.cache import cache
    gid = _get_guild_id()
    if not gid:
        return []
    key = f"schedevents.guild_emojis.{gid}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    emojis: list[dict] = []
    try:
        import requests
        from app.preferences.utils import get_global_preference
        from app.discordauth.preferences import BotToken
        bot_token = get_global_preference(BotToken.import_path)
        if not bot_token:
            return []
        r = requests.get(
            f"https://discord.com/api/v10/guilds/{gid}/emojis",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=4,
        )
        r.raise_for_status()
        for e in r.json():
            eid = e.get("id")
            name = e.get("name") or ""
            if not eid or not name:
                continue
            animated = bool(e.get("animated"))
            prefix = "a" if animated else ""
            emojis.append({
                "id": eid,
                "name": name,
                "animated": animated,
                "token": f"<{prefix}:{name}:{eid}>",
                "url": f"https://cdn.discordapp.com/emojis/{eid}.{'gif' if animated else 'png'}?size=64&quality=lossless",
            })
        emojis.sort(key=lambda x: x["name"].lower())
    except Exception:
        return []

    cache.set(key, emojis, 300)
    return emojis


def _parse_datetime(date_str: str, time_str: str, tz_name: str):
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    import datetime as dt_module
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        tz = dt_module.timezone.utc
    try:
        local_dt = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%Y-%m-%d %H:%M")
        return local_dt.replace(tzinfo=tz).astimezone(dt_module.timezone.utc).replace(tzinfo=dt_module.timezone.utc)
    except (ValueError, AttributeError):
        return None


def _parse_duration(value: str):
    from datetime import timedelta
    value = value.strip().lower()
    try:
        if "h" in value and "m" in value:
            h, m = value.split("h")
            return timedelta(hours=int(h), minutes=int(m.replace("m", "")))
        if "h" in value:
            return timedelta(hours=int(value.replace("h", "")))
        if "m" in value:
            return timedelta(minutes=int(value.replace("m", "")))
        return timedelta(minutes=int(value))
    except (ValueError, TypeError):
        return None
