import logging

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from app.celerytools.utils import QueueOnce, register_periodic_task

logger = get_task_logger(__name__)


@register_periodic_task(every=1, name="goals: send due goal reminders")
@shared_task(base=QueueOnce, once={"graceful": True})
def send_due_goal_reminders():
    from app.goals.models import GoalReminder
    from app.goals.signals import _publish

    now       = timezone.now()
    due       = GoalReminder.objects.filter(sent_at__isnull=True, remind_at__lte=now).select_related("org_goal", "leader_goal")
    fired     = 0

    for reminder in due:
        goal  = reminder.org_goal or reminder.leader_goal
        kind  = "ORG" if reminder.org_goal_id else "LEADER"
        title = goal.title if goal else "Unknown goal"

        _publish({
            "action":      "GOAL_REMINDER_DUE",
            "kind":        kind,
            "goal_pk":     goal.pk if goal else None,
            "title":       title,
            "channel_id":  reminder.channel_id,
            "message":     reminder.custom_message or f"⏰ Reminder: **{title}** is due soon.",
            "thread_channel_id": getattr(goal, "thread_channel_id", None),
        })

        reminder.sent_at = now
        reminder.save(update_fields=["sent_at"])
        fired += 1

    if fired:
        logger.info("Fired %d goal reminder(s)", fired)
