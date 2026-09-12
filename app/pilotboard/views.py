"""
app/pilotboard/views.py
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from app.pilotboard.models import GoalNote, Pilot, PilotGoal, PilotGroupFilter, PilotTag
from app.pilotboard.permissions import user_can_manage_roster, user_can_view_roster

_VALID_SKILLS = {"team_fighting", "duelling", "leadership"}


def _forbidden():
    return JsonResponse({"error": "forbidden"}, status=403)


def _bad(msg):
    return JsonResponse({"error": msg}, status=400)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    if not user_can_view_roster(request.user):
        return render(request, "pilotboard/access_denied.html", status=403)

    can_manage = user_can_manage_roster(request.user)

    # Always load group filters (needed for tag display on every card)
    all_gf = list(PilotGroupFilter.objects.select_related("group").all())
    gf_label_map = {gf.group_id: gf.display_label for gf in all_gf}
    gf_group_ids = set(gf_label_map.keys())

    pilots = (
        Pilot.objects
        .select_related("org_player")
        .prefetch_related("goals", "org_player__groups")
        .order_by("name")
    )

    if not can_manage:
        pilots = pilots.filter(org_player=request.user)
        group_filters   = []
        active_group_pk = None
        status_filter   = "all"
    else:
        status_filter = request.GET.get("status", "active")
        if status_filter == "active":
            pilots = pilots.filter(is_active=True)
        elif status_filter == "inactive":
            pilots = pilots.filter(is_active=False)

        group_filters = all_gf
        try:
            active_group_pk = int(request.GET.get("group", ""))
            pilots = pilots.filter(org_player__groups__id=active_group_pk).distinct()
        except (TypeError, ValueError):
            active_group_pk = None

    pilots_data = []
    for pilot in pilots:
        open_goal = next((g for g in pilot.goals.all() if not g.completed), None)
        group_labels = []
        if pilot.org_player_id and gf_group_ids:
            for g in pilot.org_player.groups.all():
                if g.id in gf_group_ids:
                    group_labels.append(gf_label_map[g.id])
        pilots_data.append({
            "pilot":      pilot,
            "open_goal":  open_goal,
            "tag_labels": group_labels,
        })

    return render(request, "pilotboard/dashboard.html", {
        "pilots_data":      pilots_data,
        "total":            len(pilots_data),
        "can_manage":       can_manage,
        "status_filter":    status_filter,
        "group_filters":    group_filters,
        "active_group_pk":  active_group_pk,
    })


# ---------------------------------------------------------------------------
# Pilot detail
# ---------------------------------------------------------------------------

@login_required
def pilot_detail(request, slug):
    if not user_can_view_roster(request.user):
        return render(request, "pilotboard/access_denied.html", status=403)

    pilot = get_object_or_404(Pilot, slug=slug)
    goals = list(
        pilot.goals
        .prefetch_related("notes__author")
        .order_by("created_at")
    )

    skills = [
        ("team_fighting", "Team Fighting", pilot.team_fighting),
        ("duelling",      "Duelling",      pilot.duelling),
        ("leadership",    "Leadership",    pilot.leadership),
    ]
    can_manage   = user_can_manage_roster(request.user)
    is_own_pilot = (pilot.org_player_id is not None and pilot.org_player_id == request.user.pk)

    group_filters = list(PilotGroupFilter.objects.select_related("group").all())
    pilot_group_ids = []
    if pilot.org_player_id and group_filters:
        gf_group_ids = {gf.group_id for gf in group_filters}
        pilot_group_ids = list(
            pilot.org_player.groups.filter(id__in=gf_group_ids).values_list("id", flat=True)
        )

    return render(request, "pilotboard/detail.html", {
        "pilot":              pilot,
        "goals":              goals,
        "skills":             skills,
        "can_manage":         can_manage,
        "is_own_pilot":       is_own_pilot,
        "can_interact_goals": can_manage or is_own_pilot,
        "has_org_player":     pilot.org_player_id is not None,
        # XSS-03: pass raw objects; template renders them safely via json_script.
        "group_filters_data":   [
            {"id": gf.group_id, "label": gf.display_label} for gf in group_filters
        ],
        "pilot_group_ids_data": pilot_group_ids,
    })


# ---------------------------------------------------------------------------
# AJAX – pilot mutations (all require manage perm)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def pilot_rename(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    pilot = get_object_or_404(Pilot, slug=slug)
    data = json.loads(request.body)
    name = (data.get("name") or "").strip()
    if not name:
        return _bad("name required")
    if Pilot.objects.filter(name__iexact=name).exclude(slug=slug).exists():
        return _bad("A pilot with that name already exists.")
    pilot.name = name
    pilot.updated_by = request.user
    pilot.save(update_fields=["name", "updated_by"])
    return JsonResponse({"ok": True, "name": pilot.name, "slug": pilot.slug})


@login_required
@require_POST
def pilot_update_skill(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    pilot = get_object_or_404(Pilot, slug=slug)
    data = json.loads(request.body)
    skill = data.get("skill")
    value = data.get("value")
    if skill not in _VALID_SKILLS:
        return _bad("invalid skill")
    if not isinstance(value, int) or not 0 <= value <= 5:
        return _bad("value must be 0–5")
    setattr(pilot, skill, value)
    pilot.updated_by = request.user
    pilot.save(update_fields=[skill, "updated_by"])
    return JsonResponse({"ok": True, "skill": skill, "value": value})


@login_required
@require_POST
def pilot_update_tags(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    pilot = get_object_or_404(Pilot, slug=slug)
    data = json.loads(request.body)
    tags = data.get("tags", [])
    valid = {t.value for t in PilotTag}
    tags = [t for t in tags if t in valid]
    pilot.tags = tags
    pilot.updated_by = request.user
    pilot.save(update_fields=["tags", "updated_by"])
    return JsonResponse({"ok": True, "tags": pilot.tags})


@login_required
@require_POST
def pilot_save_notes(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    pilot = get_object_or_404(Pilot, slug=slug)
    data = json.loads(request.body)
    pilot.notes = data.get("notes", "")
    pilot.notes_updated_at = timezone.now()
    pilot.updated_by = request.user
    pilot.save(update_fields=["notes", "notes_updated_at", "updated_by"])
    return JsonResponse({"ok": True, "notes_updated_at": pilot.notes_updated_at.isoformat()})


@login_required
@require_POST
def pilot_toggle_active(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    pilot = get_object_or_404(Pilot, slug=slug)
    pilot.is_active = not pilot.is_active
    pilot.updated_by = request.user
    pilot.save(update_fields=["is_active", "updated_by"])
    return JsonResponse({"ok": True, "is_active": pilot.is_active})


@login_required
@require_POST
def pilot_toggle_group(request, slug, gid):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    pilot = get_object_or_404(Pilot, slug=slug)
    if not pilot.org_player_id:
        return _bad("Pilot has no linked account; cannot manage groups.")
    gf = get_object_or_404(PilotGroupFilter, group_id=gid)
    user = pilot.org_player
    if user.groups.filter(pk=gid).exists():
        user.groups.remove(gf.group)
        in_group = False
    else:
        user.groups.add(gf.group)
        in_group = True
    return JsonResponse({"ok": True, "in_group": in_group, "group_id": gid})


def _can_interact_goals(user, pilot):
    if user_can_manage_roster(user):
        return True
    return pilot.org_player_id is not None and pilot.org_player_id == user.pk


@login_required
@require_POST
def goal_add(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    pilot = get_object_or_404(Pilot, slug=slug)
    data = json.loads(request.body)
    title = (data.get("title") or "").strip()
    if not title:
        return _bad("title required")
    if PilotGoal.objects.filter(pilot=pilot, completed=False).exists():
        return _bad("Pilot already has an open goal.")
    goal = PilotGoal.objects.create(pilot=pilot, title=title)
    return JsonResponse({
        "ok": True,
        "id": goal.pk,
        "title": goal.title,
        "completed": goal.completed,
        "created_at": goal.created_at.isoformat(),
    })


@login_required
@require_POST
def goal_complete(request, slug, gid):
    pilot = get_object_or_404(Pilot, slug=slug)
    if not _can_interact_goals(request.user, pilot):
        return _forbidden()
    goal = get_object_or_404(PilotGoal, pk=gid, pilot=pilot)
    goal.completed = not goal.completed
    goal.save(update_fields=["completed"])
    return JsonResponse({"ok": True, "completed": goal.completed})


@login_required
@require_POST
def goal_delete(request, slug, gid):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    goal = get_object_or_404(PilotGoal, pk=gid, pilot__slug=slug)
    goal.delete()
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# AJAX – goal note mutations
# ---------------------------------------------------------------------------

@login_required
@require_POST
def goal_note_add(request, slug, gid):
    pilot = get_object_or_404(Pilot, slug=slug)
    if not _can_interact_goals(request.user, pilot):
        return _forbidden()
    goal = get_object_or_404(PilotGoal, pk=gid, pilot=pilot)
    data = json.loads(request.body)
    content = (data.get("content") or "").strip()
    if not content:
        return _bad("content required")
    note = GoalNote.objects.create(goal=goal, content=content, author=request.user)
    return JsonResponse({
        "ok": True,
        "id": note.pk,
        "content": note.content,
        "author": request.user.display_name or request.user.username,
        "created_at": note.created_at.isoformat(),
    })


@login_required
@require_POST
def goal_note_delete(request, slug, gid, nid):
    pilot = get_object_or_404(Pilot, slug=slug)
    if not _can_interact_goals(request.user, pilot):
        return _forbidden()
    note = get_object_or_404(GoalNote, pk=nid, goal_id=gid, goal__pilot=pilot)
    note.delete()
    return JsonResponse({"ok": True})
