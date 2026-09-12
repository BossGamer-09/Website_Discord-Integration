"""
app/infantryboard/views.py
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from app.infantryboard.models import GoalNote, InfantryGroupFilter, InfantryTag, Soldier, SoldierGoal
from app.infantryboard.permissions import user_can_manage_roster, user_can_view_roster

_VALID_SKILLS = {"aim", "teamplay", "comms", "tactics", "leadership"}


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
        return render(request, "infantryboard/access_denied.html", status=403)

    can_manage = user_can_manage_roster(request.user)

    all_gf = list(InfantryGroupFilter.objects.select_related("group").all())
    gf_label_map = {gf.group_id: gf.display_label for gf in all_gf}
    gf_group_ids = set(gf_label_map.keys())

    soldiers = (
        Soldier.objects
        .select_related("org_player")
        .prefetch_related("goals", "org_player__groups")
        .order_by("name")
    )

    if not can_manage:
        soldiers = soldiers.filter(org_player=request.user)
        group_filters   = []
        active_group_pk = None
        status_filter   = "all"
    else:
        status_filter = request.GET.get("status", "active")
        if status_filter == "active":
            soldiers = soldiers.filter(is_active=True)
        elif status_filter == "inactive":
            soldiers = soldiers.filter(is_active=False)

        group_filters = all_gf
        try:
            active_group_pk = int(request.GET.get("group", ""))
            soldiers = soldiers.filter(org_player__groups__id=active_group_pk).distinct()
        except (TypeError, ValueError):
            active_group_pk = None

    soldiers_data = []
    for soldier in soldiers:
        open_goal = next((g for g in soldier.goals.all() if not g.completed), None)
        group_labels = []
        if soldier.org_player_id and gf_group_ids:
            for g in soldier.org_player.groups.all():
                if g.id in gf_group_ids:
                    group_labels.append(gf_label_map[g.id])
        soldiers_data.append({
            "soldier":     soldier,
            "open_goal":   open_goal,
            "tag_labels":  group_labels,
        })

    return render(request, "infantryboard/dashboard.html", {
        "soldiers_data":    soldiers_data,
        "total":            len(soldiers_data),
        "can_manage":       can_manage,
        "status_filter":    status_filter,
        "group_filters":    group_filters,
        "active_group_pk":  active_group_pk,
    })


# ---------------------------------------------------------------------------
# Soldier detail
# ---------------------------------------------------------------------------

@login_required
def soldier_detail(request, slug):
    if not user_can_view_roster(request.user):
        return render(request, "infantryboard/access_denied.html", status=403)

    soldier = get_object_or_404(Soldier, slug=slug)
    goals = list(
        soldier.goals
        .prefetch_related("notes__author")
        .order_by("created_at")
    )

    skills = [
        ("aim",        "Aim",        soldier.aim),
        ("teamplay",   "Teamplay",   soldier.teamplay),
        ("comms",      "Comms",      soldier.comms),
        ("tactics",    "Tactics",    soldier.tactics),
        ("leadership", "Leadership", soldier.leadership),
    ]
    can_manage    = user_can_manage_roster(request.user)
    is_own_entry  = (soldier.org_player_id is not None and soldier.org_player_id == request.user.pk)

    group_filters = list(InfantryGroupFilter.objects.select_related("group").all())
    soldier_group_ids = []
    if soldier.org_player_id and group_filters:
        gf_group_ids = {gf.group_id for gf in group_filters}
        soldier_group_ids = list(
            soldier.org_player.groups.filter(id__in=gf_group_ids).values_list("id", flat=True)
        )

    return render(request, "infantryboard/detail.html", {
        "soldier":              soldier,
        "goals":                goals,
        "skills":               skills,
        "can_manage":           can_manage,
        "is_own_entry":         is_own_entry,
        "can_interact_goals":   can_manage or is_own_entry,
        "has_org_player":       soldier.org_player_id is not None,
        "group_filters_json":   json.dumps([
            {"id": gf.group_id, "label": gf.display_label} for gf in group_filters
        ]),
        "soldier_group_ids_json": json.dumps(soldier_group_ids),
    })


# ---------------------------------------------------------------------------
# AJAX – soldier mutations
# ---------------------------------------------------------------------------

@login_required
@require_POST
def soldier_rename(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    soldier = get_object_or_404(Soldier, slug=slug)
    data = json.loads(request.body)
    name = (data.get("name") or "").strip()
    if not name:
        return _bad("name required")
    if Soldier.objects.filter(name__iexact=name).exclude(slug=slug).exists():
        return _bad("A soldier with that name already exists.")
    soldier.name = name
    soldier.updated_by = request.user
    soldier.save(update_fields=["name", "updated_by"])
    return JsonResponse({"ok": True, "name": soldier.name, "slug": soldier.slug})


@login_required
@require_POST
def soldier_update_skill(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    soldier = get_object_or_404(Soldier, slug=slug)
    data = json.loads(request.body)
    skill = data.get("skill")
    value = data.get("value")
    if skill not in _VALID_SKILLS:
        return _bad("invalid skill")
    if not isinstance(value, int) or not 0 <= value <= 5:
        return _bad("value must be 0–5")
    setattr(soldier, skill, value)
    soldier.updated_by = request.user
    soldier.save(update_fields=[skill, "updated_by"])
    return JsonResponse({"ok": True, "skill": skill, "value": value})


@login_required
@require_POST
def soldier_update_tags(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    soldier = get_object_or_404(Soldier, slug=slug)
    data = json.loads(request.body)
    tags = data.get("tags", [])
    valid = {t.value for t in InfantryTag}
    tags = [t for t in tags if t in valid]
    soldier.tags = tags
    soldier.updated_by = request.user
    soldier.save(update_fields=["tags", "updated_by"])
    return JsonResponse({"ok": True, "tags": soldier.tags})


@login_required
@require_POST
def soldier_save_notes(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    soldier = get_object_or_404(Soldier, slug=slug)
    data = json.loads(request.body)
    soldier.notes = data.get("notes", "")
    soldier.notes_updated_at = timezone.now()
    soldier.updated_by = request.user
    soldier.save(update_fields=["notes", "notes_updated_at", "updated_by"])
    return JsonResponse({"ok": True, "notes_updated_at": soldier.notes_updated_at.isoformat()})


@login_required
@require_POST
def soldier_toggle_active(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    soldier = get_object_or_404(Soldier, slug=slug)
    soldier.is_active = not soldier.is_active
    soldier.updated_by = request.user
    soldier.save(update_fields=["is_active", "updated_by"])
    return JsonResponse({"ok": True, "is_active": soldier.is_active})


@login_required
@require_POST
def soldier_toggle_group(request, slug, gid):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    soldier = get_object_or_404(Soldier, slug=slug)
    if not soldier.org_player_id:
        return _bad("Soldier has no linked account; cannot manage groups.")
    gf = get_object_or_404(InfantryGroupFilter, group_id=gid)
    user = soldier.org_player
    if user.groups.filter(pk=gid).exists():
        user.groups.remove(gf.group)
        in_group = False
    else:
        user.groups.add(gf.group)
        in_group = True
    return JsonResponse({"ok": True, "in_group": in_group, "group_id": gid})


def _can_interact_goals(user, soldier):
    if user_can_manage_roster(user):
        return True
    return soldier.org_player_id is not None and soldier.org_player_id == user.pk


@login_required
@require_POST
def goal_add(request, slug):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    soldier = get_object_or_404(Soldier, slug=slug)
    data = json.loads(request.body)
    title = (data.get("title") or "").strip()
    if not title:
        return _bad("title required")
    if SoldierGoal.objects.filter(soldier=soldier, completed=False).exists():
        return _bad("Soldier already has an open goal.")
    goal = SoldierGoal.objects.create(soldier=soldier, title=title)
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
    soldier = get_object_or_404(Soldier, slug=slug)
    if not _can_interact_goals(request.user, soldier):
        return _forbidden()
    goal = get_object_or_404(SoldierGoal, pk=gid, soldier=soldier)
    goal.completed = not goal.completed
    goal.save(update_fields=["completed"])
    return JsonResponse({"ok": True, "completed": goal.completed})


@login_required
@require_POST
def goal_delete(request, slug, gid):
    if not user_can_manage_roster(request.user):
        return _forbidden()
    goal = get_object_or_404(SoldierGoal, pk=gid, soldier__slug=slug)
    goal.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def goal_note_add(request, slug, gid):
    soldier = get_object_or_404(Soldier, slug=slug)
    if not _can_interact_goals(request.user, soldier):
        return _forbidden()
    goal = get_object_or_404(SoldierGoal, pk=gid, soldier=soldier)
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
    soldier = get_object_or_404(Soldier, slug=slug)
    if not _can_interact_goals(request.user, soldier):
        return _forbidden()
    note = get_object_or_404(GoalNote, pk=nid, goal_id=gid, goal__soldier=soldier)
    note.delete()
    return JsonResponse({"ok": True})
