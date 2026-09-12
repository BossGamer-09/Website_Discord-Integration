from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import (
    Avg, Count, Q, Sum, FloatField, ExpressionWrapper, F,
)
from django.db.models.functions import TruncMonth, TruncWeek
from django.shortcuts import render
from django.utils import timezone


def _access_denied(request, message=None):
    return render(request, "analytics/access_denied.html", {"message": message}, status=403)


def _require_perm(request, perm):
    return request.user.is_authenticated and (
        request.user.is_staff or request.user.is_superuser or request.user.has_perm(perm)
    )


# ---------------------------------------------------------------------------
# Org Overview
# ---------------------------------------------------------------------------

@login_required
def overview(request):
    if not _require_perm(request, "analytics.view_analytics"):
        return _access_denied(request, "You don't have permission to view Analytics.")

    from app.unifieduser.models import OrgPlayer, OrgRank
    from app.disfunction.models import UserJoinRecord
    from app.schedevents.models import EventPlan, EventRSVP
    from app.killtracker.models import KillEvent

    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)

    # Member counts
    total_members = OrgPlayer.objects.filter(is_active=True).count()
    new_this_month = OrgPlayer.objects.filter(date_joined__gte=thirty_days_ago).count()

    # Join/leave trend (last 90 days from UserJoinRecord)
    recent_joins  = UserJoinRecord.objects.filter(joined_at__gte=ninety_days_ago, is_active=True).count()
    recent_leaves = UserJoinRecord.objects.filter(
        left_at__gte=ninety_days_ago,
        membership_status__in=["LEFT", "KICKED", "BANNED"]
    ).count()

    # Rank distribution
    rank_dist = (
        OrgPlayer.objects
        .filter(is_active=True, rank__isnull=False)
        .values("rank__prefix", "rank__name")
        .annotate(count=Count("pk"))
        .order_by("-count")[:12]
    )

    # Event activity (last 30 days)
    recent_events = EventPlan.objects.filter(planned_start_at__gte=thirty_days_ago).count()
    total_rsvps   = EventRSVP.objects.filter(
        event__planned_start_at__gte=thirty_days_ago
    ).count()

    # Kill activity (last 30 days)
    recent_kills = KillEvent.objects.filter(time__gte=thirty_days_ago).count()

    # Member join trend by month (last 6 months)
    join_trend = (
        OrgPlayer.objects
        .filter(date_joined__gte=now - timedelta(days=180))
        .annotate(month=TruncMonth("date_joined"))
        .values("month")
        .annotate(count=Count("pk"))
        .order_by("month")
    )

    return render(request, "analytics/overview.html", {
        "total_members":   total_members,
        "new_this_month":  new_this_month,
        "recent_joins":    recent_joins,
        "recent_leaves":   recent_leaves,
        "rank_dist":       list(rank_dist),
        "recent_events":   recent_events,
        "total_rsvps":     total_rsvps,
        "recent_kills":    recent_kills,
        "join_trend":      list(join_trend),
    })


# ---------------------------------------------------------------------------
# Member Activity
# ---------------------------------------------------------------------------

@login_required
def member_activity(request):
    if not _require_perm(request, "analytics.view_analytics"):
        return _access_denied(request, "You don't have permission to view Analytics.")

    from app.unifieduser.models import OrgPlayer
    from app.schedevents.models import EventRSVP
    from app.disfunction.models import VoiceActivitySummary

    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)

    # Top attendees — members with most PRESENT outcomes in last 30 days
    top_attendees = (
        EventRSVP.objects
        .filter(
            attendance_outcome="PRESENT",
            event__planned_start_at__gte=thirty_days_ago,
        )
        .values("user__pk", "user__username")
        .annotate(attended=Count("pk"))
        .order_by("-attended")[:15]
    )

    # Attendance rate per member (attended / rsvp'd going, last 30 days)
    rsvp_stats = (
        EventRSVP.objects
        .filter(
            status="GOING",
            event__planned_start_at__gte=thirty_days_ago,
            user__isnull=False,
        )
        .values("user__pk", "user__username")
        .annotate(
            total_rsvp=Count("pk"),
            attended=Count("pk", filter=Q(attendance_outcome="PRESENT")),
            late=Count("pk", filter=Q(attendance_outcome="LATE")),
            absent=Count("pk", filter=Q(attendance_outcome="ABSENT")),
        )
        .filter(total_rsvp__gte=2)
        .order_by("-total_rsvp")[:20]
    )
    # compute show_rate
    rsvp_rows = []
    for row in rsvp_stats:
        show = row["attended"] + row["late"]
        rate = round((show / row["total_rsvp"]) * 100) if row["total_rsvp"] else 0
        rsvp_rows.append({**row, "show_rate": rate})

    # Top voice time (from VoiceActivitySummary MONTHLY, most recent period)
    top_voice = (
        VoiceActivitySummary.objects
        .filter(period_type="MONTHLY", period_start__gte=thirty_days_ago)
        .values("user_id")
        .annotate(
            total_hours=ExpressionWrapper(
                Sum("total_duration_seconds") / 3600.0,
                output_field=FloatField(),
            ),
            avg_talking_pct=Avg("avg_talking_percentage"),
        )
        .order_by("-total_hours")[:15]
    )
    # resolve display names
    uid_map = {}
    uids = [r["user_id"] for r in top_voice]
    if uids:
        from app.discordauth.models import DiscordUser
        for du in DiscordUser.objects.filter(discorduid__in=uids).select_related("user"):
            uid_map[du.discorduid] = du.user.display_name if du.user else str(du.discorduid)

    top_voice_rows = [
        {
            "display_name": uid_map.get(r["user_id"], str(r["user_id"])),
            "total_hours":  round(r["total_hours"] or 0, 1),
            "talking_pct":  round(r["avg_talking_pct"] or 0, 1),
        }
        for r in top_voice
    ]

    # Members with no event attendance in 30 days (inactive warning)
    attended_pks = set(
        EventRSVP.objects
        .filter(
            attendance_outcome__in=["PRESENT", "LATE"],
            event__planned_start_at__gte=thirty_days_ago,
        )
        .values_list("user_id", flat=True)
    )
    inactive_members = (
        OrgPlayer.objects
        .filter(is_active=True, first_event_at__isnull=False)
        .exclude(pk__in=attended_pks)
        .select_related("rank")
        .order_by("rank__order", "username")[:20]
    )

    return render(request, "analytics/member_activity.html", {
        "top_attendees":    list(top_attendees),
        "rsvp_rows":        rsvp_rows,
        "top_voice_rows":   top_voice_rows,
        "inactive_members": inactive_members,
        "period_label":     "Last 30 Days",
    })


# ---------------------------------------------------------------------------
# Event Health
# ---------------------------------------------------------------------------

@login_required
def event_health(request):
    if not _require_perm(request, "analytics.view_analytics"):
        return _access_denied(request, "You don't have permission to view Analytics.")

    from app.schedevents.models import EventPlan, EventRSVP, EventReport

    now = timezone.now()
    sixty_days_ago = now - timedelta(days=60)

    # Recent completed events with reports
    recent_plans = (
        EventPlan.objects
        .filter(planned_start_at__gte=sixty_days_ago, status="COMPLETED")
        .select_related("report")
        .prefetch_related("organizers")
        .order_by("-planned_start_at")[:30]
    )

    event_rows = []
    for plan in recent_plans:
        report = getattr(plan, "report", None)
        going   = EventRSVP.objects.filter(event=plan, status="GOING").count()
        present = EventRSVP.objects.filter(event=plan, attendance_outcome="PRESENT").count()
        late    = EventRSVP.objects.filter(event=plan, attendance_outcome="LATE").count()
        absent  = EventRSVP.objects.filter(event=plan, attendance_outcome="ABSENT").count()
        show    = present + late
        rate    = round((show / going) * 100) if going else None
        event_rows.append({
            "plan":    plan,
            "going":   going,
            "present": present,
            "late":    late,
            "absent":  absent,
            "show_rate": rate,
            "duration_min": (
                round((report.actual_end_at - report.actual_start_at).total_seconds() / 60)
                if report and report.actual_end_at and report.actual_start_at else None
            ),
        })

    # Aggregate stats
    total_events = len(event_rows)
    avg_show_rate = (
        round(sum(r["show_rate"] for r in event_rows if r["show_rate"] is not None)
              / max(sum(1 for r in event_rows if r["show_rate"] is not None), 1))
        if event_rows else 0
    )

    # Weekly event count trend (last 8 weeks)
    weekly_trend = (
        EventPlan.objects
        .filter(planned_start_at__gte=now - timedelta(weeks=8))
        .annotate(week=TruncWeek("planned_start_at"))
        .values("week")
        .annotate(count=Count("pk"))
        .order_by("week")
    )

    return render(request, "analytics/event_health.html", {
        "event_rows":    event_rows,
        "total_events":  total_events,
        "avg_show_rate": avg_show_rate,
        "weekly_trend":  list(weekly_trend),
        "period_label":  "Last 60 Days",
    })


# ---------------------------------------------------------------------------
# Kill Stats
# ---------------------------------------------------------------------------

@login_required
def kill_stats(request):
    if not _require_perm(request, "analytics.view_analytics"):
        return _access_denied(request, "You don't have permission to view Analytics.")

    from app.killtracker.models import KillEvent

    now = timezone.now()
    thirty_days_ago = now - timedelta(days=30)

    # Top killers (linked members, last 30 days)
    top_killers = (
        KillEvent.objects
        .filter(time__gte=thirty_days_ago, killer__isnull=False)
        .values("killer__pk", "killer_name")
        .annotate(kills=Count("pk"))
        .order_by("-kills")[:15]
    )

    # Top killers by name (includes unlinked)
    top_killers_all = (
        KillEvent.objects
        .filter(time__gte=thirty_days_ago)
        .values("killer_name")
        .annotate(kills=Count("pk"))
        .order_by("-kills")[:15]
    )

    # Kill breakdown by game mode
    by_game_mode = (
        KillEvent.objects
        .filter(time__gte=thirty_days_ago)
        .values("game_mode")
        .annotate(kills=Count("pk"))
        .order_by("-kills")
    )

    # Most killed victims (orgs/players)
    top_victims = (
        KillEvent.objects
        .filter(time__gte=thirty_days_ago)
        .values("victim")
        .annotate(count=Count("pk"))
        .order_by("-count")[:10]
    )

    # Most targeted victim orgs
    top_victim_orgs = (
        KillEvent.objects
        .filter(time__gte=thirty_days_ago, victim_org__gt="")
        .values("victim_org", "victim_org_sid")
        .annotate(count=Count("pk"))
        .order_by("-count")[:10]
    )

    # Kill trend by week (last 8 weeks)
    weekly_kills = (
        KillEvent.objects
        .filter(time__gte=now - timedelta(weeks=8))
        .annotate(week=TruncWeek("time"))
        .values("week")
        .annotate(count=Count("pk"))
        .order_by("week")
    )

    total_kills = KillEvent.objects.filter(time__gte=thirty_days_ago).count()

    return render(request, "analytics/kill_stats.html", {
        "top_killers":      list(top_killers_all),
        "by_game_mode":     list(by_game_mode),
        "top_victims":      list(top_victims),
        "top_victim_orgs":  list(top_victim_orgs),
        "weekly_kills":     list(weekly_kills),
        "total_kills":      total_kills,
        "period_label":     "Last 30 Days",
    })


# ---------------------------------------------------------------------------
# Leadership Activity
# ---------------------------------------------------------------------------

@login_required
def leadership_activity(request):
    if not _require_perm(request, "analytics.view_analytics"):
        return _access_denied(request, "You don't have permission to view Analytics.")

    from app.leadership.models import DecisionLog, DisciplinaryRecord

    now = timezone.now()
    ninety_days_ago = now - timedelta(days=90)

    # Promotions & demotions by month
    rank_changes = (
        DecisionLog.objects
        .filter(
            event_type__in=["PROMOTION", "DEMOTION"],
            created_at__gte=ninety_days_ago,
        )
        .annotate(month=TruncMonth("created_at"))
        .values("month", "event_type")
        .annotate(count=Count("pk"))
        .order_by("month", "event_type")
    )

    # Discipline actions by type
    discipline_by_type = (
        DisciplinaryRecord.objects
        .filter(issued_at__gte=ninety_days_ago)
        .values("action_type")
        .annotate(count=Count("pk"))
        .order_by("-count")
    )

    # Recent promotions
    recent_promotions = (
        DecisionLog.objects
        .filter(event_type="PROMOTION", created_at__gte=ninety_days_ago)
        .select_related("subject", "actor", "subject__rank")
        .order_by("-created_at")[:15]
    )

    # Recent discipline records
    recent_discipline = (
        DisciplinaryRecord.objects
        .filter(issued_at__gte=ninety_days_ago)
        .select_related("subject", "issued_by")
        .order_by("-issued_at")[:15]
    )

    # Most active leadership actors
    top_actors = (
        DecisionLog.objects
        .filter(created_at__gte=ninety_days_ago, actor__isnull=False)
        .values("actor__pk", "actor__username")
        .annotate(actions=Count("pk"))
        .order_by("-actions")[:10]
    )

    total_promotions = DecisionLog.objects.filter(
        event_type="PROMOTION", created_at__gte=ninety_days_ago
    ).count()
    total_discipline = DisciplinaryRecord.objects.filter(
        issued_at__gte=ninety_days_ago
    ).count()

    return render(request, "analytics/leadership_activity.html", {
        "rank_changes":       list(rank_changes),
        "discipline_by_type": list(discipline_by_type),
        "recent_promotions":  recent_promotions,
        "recent_discipline":  recent_discipline,
        "top_actors":         list(top_actors),
        "total_promotions":   total_promotions,
        "total_discipline":   total_discipline,
        "period_label":       "Last 90 Days",
    })


# ---------------------------------------------------------------------------
# Performance Scores
# ---------------------------------------------------------------------------

@login_required
def performance_scores(request):
    if not _require_perm(request, "analytics.view_analytics"):
        return _access_denied(request, "You don't have permission to view Analytics.")

    from app.unifieduser.models import OrgPlayer
    from app.schedevents.models import EventRSVP
    from app.disfunction.models import VoiceActivitySummary
    from app.infantryboard.models import Soldier
    from app.pilotboard.models import Pilot
    from app.discordauth.models import DiscordUser

    now = timezone.now()
    ninety_days_ago = now - timedelta(days=90)

    # --- Attendance Score (0-40 pts) ---
    # For each member: show_rate = (present + late) / going RSVPs
    att_stats = (
        EventRSVP.objects
        .filter(status="GOING", event__planned_start_at__gte=ninety_days_ago, user__isnull=False)
        .values("user__pk")
        .annotate(
            total_rsvp=Count("pk"),
            shown=Count("pk", filter=Q(attendance_outcome__in=["PRESENT", "LATE"])),
        )
        .filter(total_rsvp__gte=2)
    )
    att_map = {}  # user_pk -> (show_rate_pct, total_rsvp)
    for row in att_stats:
        rate = (row["shown"] / row["total_rsvp"]) * 100 if row["total_rsvp"] else 0
        att_map[row["user__pk"]] = {"att_rate": round(rate, 1), "att_rsvps": row["total_rsvp"]}

    # --- Voice Score (0-30 pts) ---
    # talking_efficiency (0-100) averaged for last 90d MONTHLY summaries
    voice_rows = (
        VoiceActivitySummary.objects
        .filter(period_type="MONTHLY", period_start__gte=ninety_days_ago)
        .values("user_id")
        .annotate(
            avg_efficiency=Avg("talking_efficiency"),
            avg_consistency=Avg("consistency_score"),
            total_hours=ExpressionWrapper(
                Sum("total_duration_seconds") / 3600.0,
                output_field=FloatField(),
            ),
        )
    )
    # Build discord uid -> OrgPlayer pk map
    uids = [r["user_id"] for r in voice_rows]
    uid_to_pk = {}
    uid_to_name = {}
    if uids:
        for du in DiscordUser.objects.filter(discorduid__in=uids).select_related("user"):
            if du.user_id:
                uid_to_pk[du.discorduid] = du.user_id
                uid_to_name[du.discorduid] = du.user.display_name
    voice_map = {}  # user_pk -> voice data
    for row in voice_rows:
        pk = uid_to_pk.get(row["user_id"])
        if not pk:
            continue
        eff = row["avg_efficiency"] or 0
        con = row["avg_consistency"] or 0
        # voice score = avg of efficiency and consistency, scaled to 0-30
        voice_score = round(((eff + con) / 2) * 0.30, 1)
        voice_map[pk] = {
            "voice_score": voice_score,
            "voice_hours": round(row["total_hours"] or 0, 1),
            "voice_eff": round(eff, 1),
        }

    # --- Combat Score (0-20 pts): Pilot skill avg ---
    # Pilot.team_fighting + Pilot.duelling both 0-5, combined avg * 4 = 0-20
    pilot_map = {}  # user_pk -> combat score
    for pilot in Pilot.objects.filter(is_active=True, org_player__isnull=False).select_related("org_player"):
        avg = (pilot.team_fighting + pilot.duelling) / 2  # 0-5
        pilot_map[pilot.org_player_id] = {"combat_score": round(avg * 4, 1), "pilot_name": pilot.name}

    # --- Infantry Score (0-10 pts): skill_total (max 25) scaled to 10 ---
    scorecard_map = {}  # user_pk -> infantry score (reuses sc_score key for template compat)
    for soldier in Soldier.objects.filter(is_active=True, org_player__isnull=False).select_related("org_player"):
        total = soldier.skill_total  # 0-25
        score = round(total / 25 * 10, 1)
        scorecard_map[soldier.org_player_id] = {"sc_score": score, "sc_label": f"{total}/25"}

    # --- Build composite rows for all active members ---
    members = (
        OrgPlayer.objects
        .filter(is_active=True)
        .select_related("rank")
        .order_by("username")
    )

    rows = []
    for m in members:
        att   = att_map.get(m.pk, {})
        voice = voice_map.get(m.pk, {})
        pilot = pilot_map.get(m.pk, {})
        sc    = scorecard_map.get(m.pk, {})

        att_score    = round(att.get("att_rate", 0) * 0.40, 1)  # 0-40
        voice_score  = voice.get("voice_score", 0)              # 0-30
        combat_score = pilot.get("combat_score", 0)             # 0-20
        sc_score     = sc.get("sc_score", 0)                    # 0-10

        composite = round(att_score + voice_score + combat_score + sc_score, 1)

        # skip members with no data at all
        if composite == 0 and not att and not voice and not pilot and not sc:
            continue

        rows.append({
            "member":        m,
            "att_rate":      att.get("att_rate", None),
            "att_rsvps":     att.get("att_rsvps", 0),
            "att_score":     att_score,
            "voice_hours":   voice.get("voice_hours", 0),
            "voice_eff":     voice.get("voice_eff", None),
            "voice_score":   voice_score,
            "combat_score":  combat_score,
            "pilot_name":    pilot.get("pilot_name", ""),
            "sc_score":      sc_score,
            "sc_label":      sc.get("sc_label", ""),
            "composite":     composite,
        })

    rows.sort(key=lambda r: r["composite"], reverse=True)

    return render(request, "analytics/performance_scores.html", {
        "rows":            rows,
        "period_label":    "Last 90 Days",
        "total":           len(rows),
        "tier_elite":      sum(1 for r in rows if r["composite"] >= 70),
        "tier_strong":     sum(1 for r in rows if 50 <= r["composite"] < 70),
        "tier_average":    sum(1 for r in rows if 30 <= r["composite"] < 50),
        "tier_developing": sum(1 for r in rows if r["composite"] < 30),
    })
