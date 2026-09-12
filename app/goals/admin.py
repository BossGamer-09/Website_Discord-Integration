from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from .models import OrgGoal, LeaderGoal, GoalReminder


class GoalReminderInline(admin.TabularInline):
    model     = GoalReminder
    extra     = 0
    fields    = ("remind_at", "channel_id", "custom_message", "sent_at")
    readonly_fields = ("sent_at",)


@admin.register(OrgGoal)
class OrgGoalAdmin(SimpleHistoryAdmin):
    list_display  = ("title", "status", "due_date", "created_by", "created_at")
    list_filter   = ("status",)
    search_fields = ("title",)
    inlines       = [GoalReminderInline]


@admin.register(LeaderGoal)
class LeaderGoalAdmin(SimpleHistoryAdmin):
    list_display  = ("title", "leader", "status", "due_date", "created_at")
    list_filter   = ("status",)
    search_fields = ("title",)
    inlines       = [GoalReminderInline]


@admin.register(GoalReminder)
class GoalReminderAdmin(admin.ModelAdmin):
    list_display = ("__str__", "remind_at", "sent_at")
    list_filter  = ("target_type",)
