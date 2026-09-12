"""
app/schedevents/tasks.py

Celery tasks for the scheduled events system.

  send_due_event_reminders   — runs every minute, fires reminder messages
  generate_event_report      — called when an event transitions to COMPLETED
  spawn_recurring_instances  — called after publish, creates next occurrence
  auto_complete_past_events  — runs every 5 min, auto-completes overdue ACTIVE events
"""
import json
import logging
from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone
from django_redis import get_redis_connection

from app.celerytools.utils import QueueOnce, register_periodic_task

logger = get_task_logger(__name__)

PUBSUB_CHANNEL = "schedevents.sync"


def _publish(payload: dict):
    r = get_redis_connection("default")
    r.publish(PUBSUB_CHANNEL, json.dumps(payload))


# ---------------------------------------------------------------------------
# Reminder sending
# ---------------------------------------------------------------------------

@register_periodic_task(every=1, name="schedevents: send due event reminders")
@shared_task(base=QueueOnce, once={"graceful": True})
def send_due_event_reminders():
    """
    Polls EventReminder rows that are due but not yet sent.
    Publishes a ReminderDue message to the bot via Redis; the bot handles the actual
    Discord DMs/channel posts.
    """
    from app.schedevents.models import EventReminder, EventPlan

    now = timezone.now()
    lookahead = now + timedelta(minutes=2)  # send up to 2 min early to avoid gaps

    due = EventReminder.objects.filter(
        sent_at__isnull=True,
        event__status__in=[EventPlan.Status.PUBLISHED, EventPlan.Status.ACTIVE],
        event__planned_start_at__gte=now,          # event hasn't started yet
        event__planned_start_at__lte=now + timedelta(days=30),  # sanity cap
    ).select_related("event").order_by("event__planned_start_at")

    fired = 0
    for reminder in due:
        if reminder.send_at > lookahead:
            continue  # too early

        _publish({
            "type": "EventReminderDue",
            "reminder_id": reminder.pk,
            "codename_id": reminder.event_id,
            "guild_id": reminder.event.guild_id,
            "minutes_before": reminder.minutes_before,
            "target": reminder.target,
            "channel_id": reminder.channel_id,
            "custom_message": reminder.custom_message,
            "announcement_channel_id": reminder.event.announcement_channel_id,
        })

        reminder.sent_at = now
        reminder.save(update_fields=["sent_at"])
        fired += 1

    if fired:
        logger.info("Fired %d event reminder(s)", fired)


# ---------------------------------------------------------------------------
# Post-event report generation
# ---------------------------------------------------------------------------

@shared_task
def generate_event_report(codename_id: str, attendance_session_id: str | None = None):
    """
    Compile attendance outcomes and create/update the EventReport row.
    Called by EventLifecycleCog after it closes the attendance session.
    Also publishes EventReportReady to Redis so the bot can post the summary embed.
    """
    from app.schedevents.models import EventPlan, EventRSVP, EventReport

    try:
        plan = EventPlan.objects.get(codename_id=codename_id)
    except EventPlan.DoesNotExist:
        logger.error("generate_event_report: EventPlan %s not found", codename_id)
        return

    rsvps = list(plan.rsvps.select_related("user__discorduser").all())
    going = [r for r in rsvps if r.status == EventRSVP.RSVPStatus.GOING]
    checked_in = [r for r in rsvps if r.checked_in]
    late = [r for r in rsvps if r.attendance_outcome == EventRSVP.AttendanceOutcome.LATE]
    no_show = [r for r in rsvps if r.attendance_outcome == EventRSVP.AttendanceOutcome.ABSENT]

    # Mark non-checked-in GOING RSVPs as absent
    for rsvp in going:
        if not rsvp.checked_in and rsvp.attendance_outcome is None:
            rsvp.attendance_outcome = EventRSVP.AttendanceOutcome.ABSENT
            rsvp._skip_signals = True
            rsvp.save(update_fields=["attendance_outcome"])
            no_show.append(rsvp)

    # Set first_event_at for members who attended for the first time
    from django.contrib.auth import get_user_model
    User = get_user_model()
    event_time = plan.planned_start_at
    for rsvp in checked_in:
        if rsvp.user_id and not rsvp.user.first_event_at:
            User.objects.filter(pk=rsvp.user_id, first_event_at__isnull=True).update(first_event_at=event_time)

    def _user_entry(rsvp):
        uid = getattr(getattr(rsvp.user, "discorduser", None), "discorduid", None)
        return {"name": rsvp.user.display_name, "uid": uid}

    # ------------------------------------------------------------------
    # VC attendance records — pull from EventAttendanceRecord
    # ------------------------------------------------------------------
    vc_records = []
    if attendance_session_id:
        try:
            from app.disfunction.models import EventAttendance, EventAttendanceRecord
            attendance = EventAttendance.objects.filter(event_id=attendance_session_id).first()
            if attendance:
                event_start = plan.planned_start_at
                event_end   = plan.planned_end_at

                # Late = joined more than 10 min after scheduled start (mirrors EventLifecycleCog logic)
                late_threshold_min = 10
                try:
                    from app.preferences.utils import get_global_preference
                    from app.schedevents import preferences as prefs
                    val = get_global_preference(prefs.EventLateThresholdMinutes.import_path)
                    if val:
                        late_threshold_min = int(val)
                except Exception:
                    pass
                late_cutoff = event_start + timedelta(minutes=late_threshold_min)

                # Left early = departed more than 10 min before the event was scheduled to end
                left_early_buffer_min = 10

                for rec in EventAttendanceRecord.objects.filter(event=attendance).order_by("join_time"):
                    join_ts = int(rec.join_time.timestamp()) if rec.join_time else None
                    leave_ts = int(rec.leave_time.timestamp()) if rec.leave_time else None

                    # Duration in seconds
                    if rec.join_time and rec.leave_time:
                        dur_s = int((rec.leave_time - rec.join_time).total_seconds())
                    elif rec.join_time and attendance.actual_end:
                        dur_s = int((attendance.actual_end - rec.join_time).total_seconds())
                    else:
                        dur_s = 0

                    # How many minutes late they joined (0 if on time)
                    joined_late_min = 0
                    if rec.join_time and rec.join_time > late_cutoff:
                        joined_late_min = int((rec.join_time - event_start).total_seconds() // 60)

                    # How many minutes before the scheduled end they left (0 if stayed)
                    left_early_min = 0
                    if rec.leave_time and rec.leave_time < (event_end - timedelta(minutes=left_early_buffer_min)):
                        left_early_min = int((event_end - rec.leave_time).total_seconds() // 60)

                    vc_records.append({
                        "uid": rec.user_id,           # Discord snowflake — already correct
                        "status": rec.status,
                        "join_ts": join_ts,
                        "leave_ts": leave_ts,
                        "duration_s": dur_s,
                        "joined_late_min": joined_late_min,
                        "left_early_min": left_early_min,
                        "was_muted": rec.was_muted,
                        "was_deafened": rec.was_deafened,
                    })
        except Exception:
            logger.exception("generate_event_report: failed to collect VC records for %s", attendance_session_id)

    report, _ = EventReport.objects.update_or_create(
        plan=plan,
        defaults={
            "actual_start_at": plan.rsvps.filter(checked_in=True).order_by("check_in_time").values_list("check_in_time", flat=True).first(),
            "actual_end_at": timezone.now(),
            "attendance_session_id": attendance_session_id,
            "voice_channel_id": plan.active_vc_channel_id,
            "total_rsvp_going": len(going),
            "total_checked_in": len(checked_in),
            "total_late": len(late),
            "total_no_show": len(no_show),
        },
    )

    _publish({
        "type": "EventReportReady",
        "codename_id": codename_id,
        "guild_id": plan.guild_id,
        "report_id": report.pk,
        "total_rsvp_going": report.total_rsvp_going,
        "total_checked_in": report.total_checked_in,
        "total_late": report.total_late,
        "total_no_show": report.total_no_show,
        "users_checked_in": [_user_entry(r) for r in checked_in if r.attendance_outcome != EventRSVP.AttendanceOutcome.LATE],
        "users_late": [_user_entry(r) for r in late],
        "users_no_show": [_user_entry(r) for r in no_show],
        "vc_records": vc_records,
        "announcement_channel_id": plan.announcement_channel_id,
        "staff_log_channel_id": None,  # lifecycle cog will resolve from prefs
    })

    logger.info(
        "EventReport generated for %s: %d going, %d checked in, %d no-show, %d VC records",
        codename_id, report.total_rsvp_going, report.total_checked_in, report.total_no_show, len(vc_records),
    )


# ---------------------------------------------------------------------------
# Recurring event spawning
# ---------------------------------------------------------------------------

@shared_task
def spawn_recurring_instances(codename_id: str):
    """
    For a parent recurring EventPlan, creates the next N occurrences up to 4 weeks ahead.
    Uses python-dateutil rrule to expand the recurrence rule.
    """
    try:
        from dateutil.rrule import rrulestr
    except ImportError:
        logger.error("spawn_recurring_instances: python-dateutil not installed")
        return

    from app.schedevents.models import EventPlan

    try:
        parent = EventPlan.objects.get(codename_id=codename_id)
    except EventPlan.DoesNotExist:
        logger.error("spawn_recurring_instances: EventPlan %s not found", codename_id)
        return

    if not parent.recurring_rule:
        return

    now = timezone.now()
    window_end = now + timedelta(weeks=4)

    try:
        rule = rrulestr(
            f"RRULE:{parent.recurring_rule}",
            dtstart=parent.planned_start_at,
            ignoretz=False,
        )
    except Exception:
        logger.exception("spawn_recurring_instances: invalid rrule '%s'", parent.recurring_rule)
        return

    # Collect existing child start times to avoid duplicates
    existing_starts = set(
        EventPlan.objects.filter(parent_event=parent)
        .values_list("planned_start_at", flat=True)
    )

    created = 0
    for occurrence_dt in rule.between(now, window_end, inc=True):
        if occurrence_dt in existing_starts:
            continue

        if parent.recurring_until and occurrence_dt > parent.recurring_until:
            break

        child = EventPlan(
            title=parent.title,
            description=parent.description,
            indicator=parent.indicator,
            created_by=parent.created_by,
            planned_start_at=occurrence_dt,
            planned_duration=parent.planned_duration,
            timezone_name=parent.timezone_name,
            guild_id=parent.guild_id,
            auto_generate_vc=parent.auto_generate_vc,
            private_thread=parent.private_thread,
            vc_profile=parent.vc_profile,
            capacity=parent.capacity,
            waitlist_enabled=parent.waitlist_enabled,
            cover_image_url=parent.cover_image_url,
            parent_event=parent,
            status=EventPlan.Status.DRAFT,
        )
        child.save()

        # Copy default reminders
        from app.schedevents.models import EventReminder
        for reminder in parent.reminders.filter(sent_at__isnull=True):
            EventReminder.objects.get_or_create(
                event=child,
                minutes_before=reminder.minutes_before,
                target=reminder.target,
                defaults={"channel_id": reminder.channel_id, "custom_message": reminder.custom_message},
            )

        created += 1

    if created:
        logger.info("Spawned %d recurring instances for %s", created, codename_id)


# ---------------------------------------------------------------------------
# Weekly event digest
# ---------------------------------------------------------------------------

@register_periodic_task(every=60 * 24 * 7, name="schedevents: post weekly event digest")
@shared_task(base=QueueOnce, once={"graceful": True})
def post_weekly_event_digest():
    """
    Runs once a week. Builds an embed listing upcoming events for the next N days,
    then posts or edits a pinned message in WeeklyDigestChannelID via Redis Pub/Sub.
    The WeeklyDigestMessageID preference is updated by the bot after posting.
    """
    from app.preferences.utils import get_global_preference
    from app.schedevents.preferences import (
        WeeklyDigestChannelID,
        WeeklyDigestMessageID,
        WeeklyDigestLookaheadDays,
        EventListingTimezone,
    )
    from app.schedevents.models import EventPlan

    channel_id = get_global_preference(WeeklyDigestChannelID.import_path)
    if not channel_id:
        return

    days    = int(get_global_preference(WeeklyDigestLookaheadDays.import_path) or 7)
    msg_id  = get_global_preference(WeeklyDigestMessageID.import_path) or 0
    tz_name = get_global_preference(EventListingTimezone.import_path) or "UTC"

    now  = timezone.now()
    end  = now + timedelta(days=days)

    events = list(
        EventPlan.objects.filter(
            status__in=[EventPlan.Status.PUBLISHED, EventPlan.Status.ACTIVE],
            planned_start_at__gte=now,
            planned_start_at__lte=end,
        ).select_related("indicator").order_by("planned_start_at")
    )

    lines = []
    for ev in events:
        ts   = int(ev.planned_start_at.timestamp())
        ind  = str(ev.indicator) if ev.indicator_id else "◻️"
        rsvp = ev.rsvps.filter(status="GOING").count()
        lines.append(f"{ind} **{ev.title}** — <t:{ts}:F>  ·  👥 {rsvp} going")

    body = "\n".join(lines) if lines else "*No events scheduled for this period.*"

    _publish({
        "type":       "WeeklyDigest",
        "channel_id": int(channel_id),
        "message_id": int(msg_id),
        "content":    f"📅 **Upcoming Events — next {days} days**\n\n{body}",
        "days":       days,
    })
    logger.info("WeeklyDigest published (%d events)", len(events))


# ---------------------------------------------------------------------------
# Auto-complete overdue ACTIVE events
# ---------------------------------------------------------------------------

@register_periodic_task(every=5, name="schedevents: auto-complete past events")
@shared_task(base=QueueOnce, once={"graceful": True})
def auto_complete_past_events():
    """
    Every 5 minutes: mark ACTIVE events whose planned end time has passed as COMPLETED
    and trigger report generation.
    """
    from app.schedevents.models import EventPlan

    now = timezone.now()
    overdue = EventPlan.objects.filter(status=EventPlan.Status.ACTIVE)

    for plan in overdue:
        if now >= plan.planned_end_at:
            plan.status = EventPlan.Status.COMPLETED
            plan.save(update_fields=["status"])  # triggers signal → EventPlanStatusChanged
            generate_event_report.delay(plan.codename_id, plan.active_vc_channel_id and str(plan.active_vc_channel_id))
            logger.info("Auto-completed overdue event %s", plan.codename_id)
