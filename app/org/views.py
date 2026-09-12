from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q, Prefetch

from app.unifieduser.models import OrgPlayer, OrgRank, DisplayNameSearchCache


def _cached_display_name(player):
    """Return the pre-built display name from cache table, falling back to nick/global name/username."""
    try:
        return player.displaynamesearchcache.display_name
    except Exception:
        pass
    try:
        du = player.discorduser
        return du.main_guild_nick or du.global_name or du.full_username or player.username
    except Exception:
        return player.username


@login_required
@permission_required("unifieduser.view_org_roster", raise_exception=True)
def roster_view(request):
    rank_filter = request.GET.get("rank", "")
    tz_filter   = request.GET.get("tz", "").strip()
    search      = request.GET.get("q", "").strip()

    qs = (
        OrgPlayer.objects
        .filter(is_active=True)
        .select_related("rank", "discorduser", "displaynamesearchcache")
        .order_by("rank__order", "username")
    )

    if rank_filter:
        qs = qs.filter(rank__name__iexact=rank_filter)

    if tz_filter:
        qs = qs.filter(timezone_str__icontains=tz_filter)

    if search:
        qs = qs.filter(
            Q(displaynamesearchcache__display_name__icontains=search) |
            Q(username__icontains=search)
        ).distinct()

    ranks = OrgRank.objects.order_by("order")

    members = []
    for m in qs[:200]:
        members.append({
            "pk":           m.pk,
            "display_name": _cached_display_name(m),
            "rank":         m.rank,
            "timezone_str": m.timezone_str,
            "nick":         m.discorduser.main_guild_nick if hasattr(m, 'discorduser') and m.discorduser else None,
        })

    ctx = {
        "members":     members,
        "ranks":       ranks,
        "rank_filter": rank_filter,
        "tz_filter":   tz_filter,
        "search":      search,
    }
    return render(request, "org/roster.html", ctx)


@login_required
@permission_required("unifieduser.view_org_roster", raise_exception=True)
def roster_member(request, pk):
    member = get_object_or_404(
        OrgPlayer.objects.select_related(
            "rank", "discorduser", "displaynamesearchcache"
        ),
        pk=pk, is_active=True,
    )

    application = None
    try:
        from app.candidacy.models import MembershipApplicationRecord
        application = (
            MembershipApplicationRecord.objects
            .filter(discorduser__user=member)
            .select_related(
                "primary_focus",
                "referred_by__discorduser",
                "referred_by__displaynamesearchcache",
                "reviewed_by__discorduser",
                "reviewed_by__displaynamesearchcache",
            )
            .order_by("-submitted_at")
            .first()
        )
    except Exception:
        pass

    discord_user = getattr(member, 'discorduser', None)

    # Pre-resolve names that would otherwise lazy-load in template
    app_data = None
    if application:
        app_data = {
            "discipline":   application.primary_focus.name if application.primary_focus else "—",
            "referred_by":  _cached_display_name(application.referred_by) if application.referred_by else (application.referral_note or "—"),
            "approved_by":  _cached_display_name(application.reviewed_by) if application.reviewed_by else "—",
            "reviewed_at":  application.reviewed_at,
            "rsi_handle":   application.rsi_handle,
            "submitted_at": application.submitted_at,
            "status":       application.get_status_display(),
        }

    referrals = []
    try:
        from app.candidacy.models import MembershipApplicationRecord
        for ref in (
            MembershipApplicationRecord.objects
            .filter(referred_by=member)
            .select_related("applicant__displaynamesearchcache", "applicant__discorduser")
            .order_by("-submitted_at")[:20]
        ):
            try:
                ref_player = ref.applicant
                ref_name = _cached_display_name(ref_player) if ref_player else "—"
                ref_pk   = ref_player.pk if ref_player else None
            except Exception:
                ref_name, ref_pk = "—", None
            referrals.append({
                "name":         ref_name,
                "pk":           ref_pk,
                "status":       ref.get_status_display(),
                "submitted_at": ref.submitted_at,
            })
    except Exception:
        pass

    # Event attendance — keyed by discord_id (BigInt) on EventAttendanceRecord
    events = []
    discord_uid = getattr(discord_user, 'discorduid', None)
    if discord_uid:
        try:
            from app.disfunction.models import EventAttendanceRecord
            for rec in (
                EventAttendanceRecord.objects
                .filter(user_id=discord_uid)
                .select_related("event")
                .order_by("-event__actual_start")[:20]
            ):
                events.append({
                    "name":       rec.event.event_name,
                    "type":       rec.event.get_event_type_display(),
                    "date":       rec.event.actual_start,
                    "status":     rec.get_status_display(),
                    "score":      round(rec.participation_score),
                })
        except Exception:
            pass

    # Internal notes
    notes = []
    try:
        from app.org.models import OrgPlayerNote
        notes = list(
            OrgPlayerNote.objects.filter(user=member).order_by("-date")[:10]
        )
    except Exception:
        pass

    ctx = {
        "member":       member,
        "display_name": _cached_display_name(member),
        "discord_user": discord_user,
        "app_data":     app_data,
        "referrals":    referrals,
        "events":       events,
        "notes":        notes,
    }
    return render(request, "org/roster_member.html", ctx)
