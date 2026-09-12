"""
app/goals/models.py

Org-wide goals and per-leader goals.
Goals can be posted to a Discord thread and have optional reminder schedules.
"""
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class OrgGoal(models.Model):
    class Status(models.TextChoices):
        OPEN       = "OPEN",      "Open"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED  = "COMPLETED", "Completed"
        CANCELLED  = "CANCELLED", "Cancelled"

    title       = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    due_date    = models.DateTimeField(null=True, blank=True)

    # Discord thread where updates are posted
    thread_channel_id  = models.BigIntegerField(null=True, blank=True, help_text="Discord thread ID for goal updates")
    thread_message_id  = models.BigIntegerField(null=True, blank=True, help_text="Pinned status message in thread")

    created_by  = models.ForeignKey(
        "unifieduser.OrgPlayer", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_org_goals",
    )
    assigned_to = models.ManyToManyField(
        "unifieduser.OrgPlayer", blank=True, related_name="assigned_org_goals",
    )

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ["-created_at"]
        permissions = [
            ("manage_org_goals", "Can create, edit, and complete org goals"),
            ("view_org_goals",   "Can view org goals"),
        ]

    def __str__(self):
        return self.title


class LeaderGoal(models.Model):
    class Status(models.TextChoices):
        OPEN       = "OPEN",      "Open"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED  = "COMPLETED", "Completed"
        CANCELLED  = "CANCELLED", "Cancelled"

    title       = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    due_date    = models.DateTimeField(null=True, blank=True)

    leader      = models.ForeignKey(
        "unifieduser.OrgPlayer", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="leader_goals",
    )

    # Discord thread for this goal
    thread_channel_id = models.BigIntegerField(null=True, blank=True)
    thread_message_id = models.BigIntegerField(null=True, blank=True)

    created_by  = models.ForeignKey(
        "unifieduser.OrgPlayer", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_leader_goals",
    )

    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ["-created_at"]
        permissions = [
            ("manage_leader_goals", "Can create, edit, and complete leader goals"),
            ("view_leader_goals",   "Can view leader goals"),
        ]

    def __str__(self):
        return f"{self.title} ({self.leader})"


class GoalReminder(models.Model):
    """Scheduled reminder for an org or leader goal."""

    class TargetType(models.TextChoices):
        ORG_GOAL    = "ORG",    "Org Goal"
        LEADER_GOAL = "LEADER", "Leader Goal"

    target_type   = models.CharField(max_length=10, choices=TargetType.choices)
    org_goal      = models.ForeignKey(OrgGoal,    null=True, blank=True, on_delete=models.CASCADE, related_name="reminders")
    leader_goal   = models.ForeignKey(LeaderGoal, null=True, blank=True, on_delete=models.CASCADE, related_name="reminders")

    remind_at     = models.DateTimeField(db_index=True)
    channel_id    = models.BigIntegerField(null=True, blank=True, help_text="Override channel; falls back to preference")
    custom_message = models.TextField(blank=True, default="")
    sent_at       = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["remind_at"]

    def __str__(self):
        goal = self.org_goal or self.leader_goal
        return f"Reminder for {goal} at {self.remind_at}"
