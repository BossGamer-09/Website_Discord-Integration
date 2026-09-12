from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse

from app.sc_tracker.models import (
    HangarStatusMessage,
    RSIIssue,
    RSIStatusSnapshot,
    SCTrackerConfig,
    StatusChannelMessage,
    StatusDMMessage,
    StatusSubscription,
)


@admin.register(SCTrackerConfig)
class SCTrackerConfigAdmin(admin.ModelAdmin):
    """
    Singleton config — always redirects to the single record's change page.
    Set channel IDs, role IDs and hangar timing constants here.
    """
    fieldsets = (
        ("Discord Channels", {
            "fields": ("hangar_channel_id", "status_channel_id", "updates_channel_id"),
            "description": "Channel IDs where the embeds will be posted.",
        }),
        ("Discord Roles (0 = no ping)", {
            "fields": ("hangar_phase_role_id", "hangar_light_role_id"),
        }),
        ("Hangar Timing — update after each SC patch", {
            "fields": ("hangar_t0_ms", "hangar_open_ms", "hangar_close_ms", "hangar_patch_info"),
            "description": (
                "Get these values from <a href='https://exec.xyxyll.com/app.js' target='_blank'>"
                "exec.xyxyll.com/app.js</a> after each Star Citizen patch."
            ),
        }),
        ("Feature Flags", {
            "fields": ("hangar_enabled", "status_enabled"),
        }),
    )

    def has_add_permission(self, request):
        return not SCTrackerConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Skip the list page — go straight to the single record
        obj, _ = SCTrackerConfig.objects.get_or_create(id=1)
        return HttpResponseRedirect(
            reverse("admin:sc_tracker_sctrackerconfig_change", args=[obj.pk])
        )


@admin.register(RSIIssue)
class RSIIssueAdmin(admin.ModelAdmin):
    list_display   = ("title", "severity", "resolved", "first_seen", "updated_at")
    list_filter    = ("resolved", "severity", "informational")
    search_fields  = ("title", "filename")
    readonly_fields = ("filename", "first_seen", "updated_at")
    ordering       = ("-first_seen",)

    def has_add_permission(self, request):
        return False


@admin.register(RSIStatusSnapshot)
class RSIStatusSnapshotAdmin(admin.ModelAdmin):
    list_display   = ("id", "fetched_at")
    readonly_fields = ("id", "raw_json", "fetched_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StatusSubscription)
class StatusSubscriptionAdmin(admin.ModelAdmin):
    list_display   = ("discord_user_id", "is_active", "created_at", "last_notified")
    list_filter    = ("is_active",)
    search_fields  = ("discord_user_id",)
    readonly_fields = ("created_at",)


@admin.register(StatusDMMessage)
class StatusDMMessageAdmin(admin.ModelAdmin):
    list_display   = ("discord_user_id", "message_type", "incident_title", "created_at")
    list_filter    = ("message_type",)
    search_fields  = ("discord_user_id", "incident_guid", "incident_title")
    readonly_fields = ("created_at",)
    ordering       = ("-created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(StatusChannelMessage)
class StatusChannelMessageAdmin(admin.ModelAdmin):
    list_display   = ("channel_id", "message_id", "message_type", "last_updated")
    readonly_fields = ("last_updated",)

    def has_add_permission(self, request):
        return False


@admin.register(HangarStatusMessage)
class HangarStatusMessageAdmin(admin.ModelAdmin):
    list_display   = ("id", "message_id", "last_updated", "notification_count")
    readonly_fields = ("last_updated",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False