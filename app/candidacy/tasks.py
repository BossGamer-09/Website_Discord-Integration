import json
import logging
import random
from datetime import date, timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from app.celerytools.utils import QueueOnce, register_periodic_task

logger = get_task_logger(__name__)

CANDIDACY_PUBSUB = "app.candidacy.notify"


def _publish(payload: dict):
    try:
        from django_redis import get_redis_connection
        r = get_redis_connection("default")
        r.publish(CANDIDACY_PUBSUB, json.dumps(payload))
    except Exception as e:
        logger.error("candidacy tasks publish error: %s", e)


@register_periodic_task(every=60 * 24 * 7, name="candidacy: rotate weekly onboarding leaders")
@shared_task(base=QueueOnce, once={"graceful": True})
def rotate_onboarding_leaders():
    """
    Weekly: pick 1-2 leaders from eligible groups and post to OnboardingLeaderChannelID.
    Skips if OnboardingLeaderRotationEnabled is OFF or no eligible leaders found.
    """
    from app.preferences.utils import get_global_preference
    from app.candidacy.preferences import (
        OnboardingLeaderRotationEnabled,
        OnboardingLeaderGroupPKs,
    )
    from app.candidacy.models import OnboardingLeaderAssignment
    from app.unifieduser.models import OrgPlayer

    if not get_global_preference(OnboardingLeaderRotationEnabled.import_path):
        return

    group_pks_raw = get_global_preference(OnboardingLeaderGroupPKs.import_path) or ""
    group_pks     = [int(p.strip()) for p in group_pks_raw.split(",") if p.strip().isdigit()]

    if not group_pks:
        logger.warning("rotate_onboarding_leaders: no OnboardingLeaderGroupPKs configured")
        return

    eligible = list(
        OrgPlayer.objects.filter(
            groups__pk__in=group_pks, is_active=True,
        ).distinct().select_related("discorduser")
    )

    if not eligible:
        logger.warning("rotate_onboarding_leaders: no eligible leaders found")
        return

    count    = min(2, len(eligible))
    selected = random.sample(eligible, count)

    week_start = date.today() - timedelta(days=date.today().weekday())  # Monday
    assignment, _ = OnboardingLeaderAssignment.objects.get_or_create(week_start=week_start)
    assignment.leaders.set(selected)

    discord_ids = []
    for leader in selected:
        try:
            discord_ids.append(str(leader.discorduser.discorduid))
        except Exception:
            discord_ids.append(None)

    _publish({
        "action":       "ONBOARDING_LEADERS_ASSIGNED",
        "week_start":   week_start.isoformat(),
        "leader_names": [str(l) for l in selected],
        "discord_ids":  discord_ids,
        "assignment_pk": assignment.pk,
    })

    logger.info("Onboarding leaders for w/c %s: %s", week_start, [str(l) for l in selected])


# ---------------------------------------------------------------------------
# Auto-close abandoned applications
# ---------------------------------------------------------------------------

@register_periodic_task(every=60 * 24, name="candidacy: auto-close abandoned applications")
@shared_task(base=QueueOnce, once={"graceful": True})
def auto_close_abandoned_applications():
    """
    Daily: deny PENDING applications that have had no activity for MembershipAutoCloseDays days
    and publish a Redis event so the bot can lock the thread.
    """
    from app.preferences.utils import get_global_preference
    from app.candidacy.preferences import MembershipAutoCloseDays
    from app.candidacy.models import MembershipApplicationRecord

    days = get_global_preference(MembershipAutoCloseDays.import_path)
    if not days:
        return

    cutoff = timezone.now() - timedelta(days=int(days))
    stale = MembershipApplicationRecord.objects.filter(
        status=MembershipApplicationRecord.Status.PENDING,
        submitted_at__lt=cutoff,
    )
    count = 0
    for record in stale:
        record.status = MembershipApplicationRecord.Status.DENIED
        record.save(update_fields=["status"])
        _publish({
            "action":     "APPLICATION_AUTO_CLOSED",
            "record_pk":  record.pk,
            "thread_id":  record.thread_id,
            "reason":     f"No activity for {days} days.",
        })
        count += 1

    if count:
        logger.info("auto_close_abandoned_applications: closed %d records", count)


# ---------------------------------------------------------------------------
# Follow-up DM to newly approved members
# ---------------------------------------------------------------------------

@register_periodic_task(every=60 * 6, name="candidacy: send follow-up DMs")
@shared_task(base=QueueOnce, once={"graceful": True})
def send_followup_dms():
    """
    Every 6 hours: DM approved members who haven't received a follow-up yet,
    if MembershipFollowUpReminderDays days have passed since approval.
    """
    from app.preferences.utils import get_global_preference
    from app.candidacy.preferences import MembershipFollowUpReminderDays, MembershipFollowUpMessage
    from app.candidacy.models import MembershipApplicationRecord

    reminder_days = get_global_preference(MembershipFollowUpReminderDays.import_path)
    if not reminder_days:
        return

    message_tpl = get_global_preference(MembershipFollowUpMessage.import_path) or ""
    cutoff = timezone.now() - timedelta(days=int(reminder_days))

    pending_followups = MembershipApplicationRecord.objects.filter(
        status=MembershipApplicationRecord.Status.APPROVED,
        follow_up_sent=False,
        reviewed_at__lt=cutoff,
        reviewed_at__isnull=False,
    ).select_related("applicant__discorduser")

    for record in pending_followups:
        try:
            du = record.applicant.discorduser
            discord_id = du.discorduid if du else None
        except Exception:
            discord_id = None

        if not discord_id:
            continue

        member_name = str(record.applicant)
        message = message_tpl.replace("{member}", f"<@{discord_id}>").replace("{member_name}", member_name)

        _publish({
            "action":     "FOLLOWUP_DM",
            "discord_id": discord_id,
            "message":    message,
            "record_pk":  record.pk,
        })

        record.follow_up_sent = True
        record.save(update_fields=["follow_up_sent"])

    logger.debug("send_followup_dms complete")
