"""
app/schedevents/admin.py

Django admin for the scheduled events system.
Provides full management UI: event lifecycle, RSVP roster, report summary.
"""
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin

from app.schedevents.models import (
    EventAttendeeRole,
    EventIndicator,
    EventPlan,
    EventReminder,
    EventReport,
    EventRoleRestriction,
    EventRSVP,
    EventTemplate,
    RSVPOption,
    TemplateCategory,
)


# ---------------------------------------------------------------------------
# EventIndicator
# ---------------------------------------------------------------------------

@admin.register(EventIndicator)
class EventIndicatorAdmin(admin.ModelAdmin):
    list_display = ("id", "colored_prefix", "name", "description")
    search_fields = ("id", "name")

    def colored_prefix(self, obj):
        return format_html(
            '<span style="color:{};">{}</span>', obj.color_hex, obj.prefix
        )
    colored_prefix.short_description = "Prefix"


# ---------------------------------------------------------------------------
# EventReminder inline
# ---------------------------------------------------------------------------

class EventReminderInline(admin.TabularInline):
    model = EventReminder
    extra = 1
    fields = ("minutes_before", "target", "channel_id", "title_template", "mentions", "sent_at")
    readonly_fields = ("sent_at",)


class RSVPOptionInline(admin.TabularInline):
    model = RSVPOption
    extra = 1
    fields = ("sort_order", "emoji", "label", "maps_to_status", "capacity_limit", "reminders_enabled", "is_active")


class EventAttendeeRoleInline(admin.TabularInline):
    model = EventAttendeeRole
    extra = 1
    fields = ("role_id", "add_time", "remove_on_end")


class EventRoleRestrictionInline(admin.TabularInline):
    model = EventRoleRestriction
    extra = 1
    fields = ("role_id", "rsvp_option")


# ---------------------------------------------------------------------------
# EventRSVP inline
# ---------------------------------------------------------------------------

class EventRSVPInline(admin.TabularInline):
    model = EventRSVP
    extra = 0
    fields = ("user", "status", "rsvp_time", "checked_in", "check_in_time", "check_in_source", "attendance_outcome")
    readonly_fields = ("rsvp_time", "check_in_time", "check_in_source")
    autocomplete_fields = ("user",)
    can_delete = True
    show_change_link = True


# ---------------------------------------------------------------------------
# EventReport inline
# ---------------------------------------------------------------------------

class EventReportInline(admin.StackedInline):
    model = EventReport
    extra = 0
    fields = (
        "actual_start_at", "actual_end_at",
        "total_rsvp_going", "total_checked_in", "total_late", "total_no_show",
        "voice_channel_id", "attendance_session_id", "summary", "generated_at",
    )
    readonly_fields = ("generated_at",)


# ---------------------------------------------------------------------------
# EventPlan
# ---------------------------------------------------------------------------

@admin.register(EventPlan)
class EventPlanAdmin(SimpleHistoryAdmin):
    list_display = (
        "codename_id", "title", "indicator", "status_badge",
        "planned_start_at", "going_count_display", "created_by",
    )
    list_filter = ("status", "indicator", "auto_generate_vc")
    search_fields = ("codename_id", "title", "description")
    readonly_fields = (
        "codename_id", "created_at", "updated_at",
        "going_count_display", "maybe_count_display", "waitlist_count_display",
    )
    autocomplete_fields = ("created_by", "organizers", "vc_profile")
    filter_horizontal = ("organizers",)
    date_hierarchy = "planned_start_at"
    ordering = ("planned_start_at",)

    fieldsets = (
        ("Identity", {
            "fields": ("codename_id", "title", "description", "indicator", "cover_image_url", "color_hex", "hide_attendees"),
        }),
        ("People", {
            "fields": ("created_by", "organizers"),
        }),
        ("Schedule", {
            "fields": (
                "planned_start_at", "planned_duration", "timezone_name",
                "recurring_rule", "recurring_until", "parent_event",
            ),
        }),
        ("Status", {
            "fields": ("status",),
        }),
        ("Discord", {
            "fields": (
                "guild_id", "discord_scheduled_event_id",
                "announcement_channel_id", "announcement_message_id",
                "attendee_thread_id", "lookback_thread_id",
            ),
            "classes": ("collapse",),
        }),
        ("Voice Channel", {
            "fields": ("auto_generate_vc", "vc_profile", "active_vc_channel_id"),
        }),
        ("RSVP Config", {
            "fields": ("capacity", "waitlist_enabled", "rsvp_deadline"),
        }),
        ("Sesh-Compatible Settings", {
            "fields": (
                "allow_maintain_rsvp",
                "mentions_on_create", "mentions_on_start", "mentions_on_vc_unlock",
            ),
            "classes": ("collapse",),
        }),
        ("Thread Settings", {
            "fields": (
                "thread_title_template", "thread_auto_archive_duration",
                "thread_join_on_rsvp", "thread_start_message_type",
            ),
            "classes": ("collapse",),
        }),
        ("RSVP Counts (live)", {
            "fields": ("going_count_display", "maybe_count_display", "waitlist_count_display"),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    inlines = [
        EventReminderInline, EventRSVPInline, EventReportInline,
        RSVPOptionInline, EventAttendeeRoleInline, EventRoleRestrictionInline,
    ]

    actions = ["action_publish", "action_cancel", "action_complete", "action_retrigger_discord", "action_fix_guild_id"]

    def status_badge(self, obj):
        colors = {
            "DRAFT":     "#6c757d",
            "PUBLISHED": "#0d6efd",
            "ACTIVE":    "#198754",
            "COMPLETED": "#6f42c1",
            "CANCELLED": "#dc3545",
        }
        color = colors.get(obj.status, "#000")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;">{}</span>',
            color, obj.get_status_display(),
        )
    status_badge.short_description = "Status"
    status_badge.admin_order_field = "status"

    def going_count_display(self, obj):
        return obj.going_count
    going_count_display.short_description = "Going"

    def maybe_count_display(self, obj):
        return obj.maybe_count
    maybe_count_display.short_description = "Maybe"

    def waitlist_count_display(self, obj):
        return obj.waitlist_count
    waitlist_count_display.short_description = "Waitlist"

    @admin.action(description="Publish selected events")
    def action_publish(self, request, queryset):
        updated = queryset.filter(status=EventPlan.Status.DRAFT).update(status=EventPlan.Status.PUBLISHED)
        self.message_user(request, f"{updated} event(s) published.")

    @admin.action(description="Cancel selected events")
    def action_cancel(self, request, queryset):
        updated = queryset.exclude(
            status__in=[EventPlan.Status.COMPLETED, EventPlan.Status.CANCELLED]
        ).update(status=EventPlan.Status.CANCELLED)
        self.message_user(request, f"{updated} event(s) cancelled.")

    @admin.action(description="Mark selected events as Completed")
    def action_complete(self, request, queryset):
        updated = queryset.filter(status=EventPlan.Status.ACTIVE).update(status=EventPlan.Status.COMPLETED)
        self.message_user(request, f"{updated} event(s) marked completed.")

    @admin.action(description="🔁 Retrigger Discord publish (re-sends IPC to bot)")
    def action_retrigger_discord(self, request, queryset):
        from app.schedevents.signals import publish
        count = 0
        for plan in queryset.filter(status=EventPlan.Status.PUBLISHED).select_related("indicator", "vc_profile"):
            publish({
                "type": "EventPlanStatusChanged",
                "codename_id": plan.codename_id,
                "status": EventPlan.Status.PUBLISHED,
                "status_before": EventPlan.Status.DRAFT,
                "guild_id": plan.guild_id,
                "title": plan.title,
                "planned_start_at": plan.planned_start_at.isoformat() if plan.planned_start_at else None,
                "discord_scheduled_event_id": plan.discord_scheduled_event_id,
                "announcement_channel_id": plan.announcement_channel_id,
                "announcement_message_id": plan.announcement_message_id,
                "auto_generate_vc": plan.auto_generate_vc,
                "vc_profile_slug": plan.vc_profile.slug if plan.vc_profile_id else None,
            })
            count += 1
        self.message_user(request, f"Retrigger signal sent for {count} published event(s). Check bot logs.")

    @admin.action(description="🔧 Fix guild_id=0 (set from BotGuildID preference)")
    def action_fix_guild_id(self, request, queryset):
        try:
            from app.preferences.utils import get_global_preference
            from app.discordauth.preferences import BotGuildID
            gid = int(get_global_preference(BotGuildID.import_path) or 0)
        except Exception:
            self.message_user(request, "Could not read BotGuildID preference.", level="error")
            return
        if not gid:
            self.message_user(request, "BotGuildID preference is not set.", level="error")
            return
        updated = queryset.filter(guild_id=0).update(guild_id=gid)
        self.message_user(request, f"Fixed guild_id on {updated} event(s) → {gid}.")


# ---------------------------------------------------------------------------
# EventRSVP standalone
# ---------------------------------------------------------------------------

@admin.register(EventRSVP)
class EventRSVPAdmin(SimpleHistoryAdmin):
    list_display = ("event", "user", "status", "checked_in", "check_in_time", "attendance_outcome", "rsvp_time")
    list_filter = ("status", "checked_in", "attendance_outcome", "is_anonymous")
    search_fields = ("event__codename_id", "event__title", "user__username")
    readonly_fields = ("rsvp_time",)
    autocomplete_fields = ("event", "user")
    date_hierarchy = "rsvp_time"

    actions = ["action_force_checkin", "action_mark_absent"]

    @admin.action(description="Force check-in selected RSVPs")
    def action_force_checkin(self, request, queryset):
        now = timezone.now()
        updated = queryset.filter(checked_in=False).update(
            checked_in=True,
            check_in_time=now,
            check_in_source=EventRSVP.CheckInSource.MANUAL,
        )
        self.message_user(request, f"{updated} RSVPs checked in.")

    @admin.action(description="Mark selected RSVPs as Absent")
    def action_mark_absent(self, request, queryset):
        updated = queryset.filter(checked_in=False).update(
            attendance_outcome=EventRSVP.AttendanceOutcome.ABSENT
        )
        self.message_user(request, f"{updated} RSVPs marked absent.")


# ---------------------------------------------------------------------------
# EventReport standalone
# ---------------------------------------------------------------------------

@admin.register(EventReport)
class EventReportAdmin(admin.ModelAdmin):
    list_display = ("plan", "total_rsvp_going", "total_checked_in", "total_late", "total_no_show", "generated_at")
    search_fields = ("plan__codename_id", "plan__title")
    readonly_fields = ("generated_at",)
    date_hierarchy = "generated_at"


# ---------------------------------------------------------------------------
# EventTemplate
# ---------------------------------------------------------------------------

@admin.register(TemplateCategory)
class TemplateCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order")
    search_fields = ("name",)


@admin.register(EventTemplate)
class EventTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "indicator", "default_duration", "auto_generate_vc", "is_active")
    list_filter = ("is_active", "category", "auto_generate_vc", "indicator")
    search_fields = ("name", "description")
    autocomplete_fields = ("indicator", "vc_profile", "created_by")


# ---------------------------------------------------------------------------
# RSVPOption
# ---------------------------------------------------------------------------

@admin.register(RSVPOption)
class RSVPOptionAdmin(admin.ModelAdmin):
    list_display = ("emoji", "label", "maps_to_status", "capacity_limit", "reminders_enabled", "sort_order", "is_active", "event")
    list_filter = ("maps_to_status", "is_active", "reminders_enabled")
    search_fields = ("label", "emoji")
    list_editable = ("sort_order", "is_active")
    ordering = ("guild_id", "sort_order")


# ---------------------------------------------------------------------------
# EventAttendeeRole
# ---------------------------------------------------------------------------

@admin.register(EventAttendeeRole)
class EventAttendeeRoleAdmin(admin.ModelAdmin):
    list_display = ("event", "role_id", "add_time", "remove_on_end")
    list_filter = ("add_time", "remove_on_end")
    search_fields = ("event__codename_id", "event__title")
    autocomplete_fields = ("event",)


# ---------------------------------------------------------------------------
# EventRoleRestriction
# ---------------------------------------------------------------------------

@admin.register(EventRoleRestriction)
class EventRoleRestrictionAdmin(admin.ModelAdmin):
    list_display = ("event", "role_id", "rsvp_option")
    search_fields = ("event__codename_id", "event__title")
    autocomplete_fields = ("event", "rsvp_option")
