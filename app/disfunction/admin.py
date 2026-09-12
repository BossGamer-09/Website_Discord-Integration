from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    VoiceChannelProfile,
    VCRolePermission,
    ProtectedChannelAck,
    TemporaryVoiceChannel,
    VoiceBan,
    VoiceSession,
    VoiceSessionCheckpoint,
    VoiceActivitySummary,
    EventAttendance,
    EventAttendanceRecord,
    Nomination,
    DistinctionLevel,
    UserDistinction,
    EventReference,
    UserJoinRecord,
)


class VCRolePermissionInline(admin.TabularInline):
    model = VCRolePermission
    extra = 1
    fields = (
        "discord_role_id", "role_label",
        "allow_view_channel", "allow_connect", "allow_speak",
        "allow_stream", "allow_use_soundboard",
        "allow_mute_members", "allow_deafen_members", "allow_move_members",
    )


@admin.register(VoiceChannelProfile)
class VoiceChannelProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "channel_class", "prefix", "required_group", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    list_filter = ("channel_class", "is_active", "apply_voice_ban", "allow_knights_controls")
    search_fields = ("name", "slug")
    ordering = ("sort_order",)
    inlines = [VCRolePermissionInline]
    fieldsets = (
        (None, {
            "fields": ("name", "slug", "channel_class", "prefix", "required_group", "sort_order", "is_active"),
        }),
        ("Behaviour Flags", {
            "fields": ("apply_voice_ban", "show_red_warning", "allow_type_switch", "allow_knights_controls"),
        }),
    )


@admin.register(ProtectedChannelAck)
class ProtectedChannelAckAdmin(admin.ModelAdmin):
    list_display = ("user_id", "last_acknowledged_at")
    search_fields = ("user_id",)
    ordering = ("-last_acknowledged_at",)
    readonly_fields = ("last_acknowledged_at",)


@admin.register(TemporaryVoiceChannel)
class TemporaryVoiceChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "channel_type", "profile_slug", "creator_id", "guild_id", "is_active", "is_paused", "created_at")
    list_filter = ("channel_type", "is_active", "is_paused")
    search_fields = ("name", "channel_id", "creator_id", "guild_id", "profile_slug")
    readonly_fields = ("channel_id", "guild_id", "parent_channel_id", "creator_id", "created_at", "last_updated")
    ordering = ("-created_at",)


@admin.register(VoiceBan)
class VoiceBanAdmin(SimpleHistoryAdmin):
    list_display = ("user_id", "kind", "invoked_at", "expires_at", "reason")
    list_filter = ("kind",)
    search_fields = ("user_id", "reason", "comment")
    ordering = ("-invoked_at",)


@admin.register(VoiceSession)
class VoiceSessionAdmin(admin.ModelAdmin):
    list_display = ("user_id", "channel_name", "channel_type", "join_time", "leave_time", "duration_seconds", "session_quality")
    list_filter = ("session_quality", "was_muted", "was_deafened", "was_afk", "ended_normally")
    search_fields = ("user_id", "channel_name", "channel_id", "guild_id")
    readonly_fields = ("duration_seconds", "talking_time_seconds", "talking_percentage", "session_quality")
    ordering = ("-join_time",)
    date_hierarchy = "join_time"


@admin.register(VoiceSessionCheckpoint)
class VoiceSessionCheckpointAdmin(admin.ModelAdmin):
    list_display = ("checkpoint_id", "user_id", "channel_name", "join_time", "is_active", "last_update")
    list_filter = ("is_active", "is_muted", "is_deafened", "is_afk")
    search_fields = ("checkpoint_id", "user_id", "channel_name")
    ordering = ("-created_at",)


@admin.register(VoiceActivitySummary)
class VoiceActivitySummaryAdmin(admin.ModelAdmin):
    list_display = ("user_id", "period_type", "period_start", "total_sessions", "total_duration_seconds", "talking_efficiency")
    list_filter = ("period_type",)
    search_fields = ("user_id", "guild_id")
    ordering = ("-period_start",)
    date_hierarchy = "period_start"


class EventAttendanceRecordInline(admin.TabularInline):
    model = EventAttendanceRecord
    extra = 0
    readonly_fields = ("participation_score", "attendance_duration_display")
    fields = ("user_id", "status", "join_time", "leave_time", "total_talking_seconds", "participation_score", "was_active_participant")

    def attendance_duration_display(self, obj):
        return f"{obj.attendance_duration}s" if obj.pk else "-"
    attendance_duration_display.short_description = "Duration"


@admin.register(EventAttendance)
class EventAttendanceAdmin(admin.ModelAdmin):
    list_display = ("event_name", "event_type", "organizer_id", "actual_start", "actual_end", "total_participants", "is_active")
    list_filter = ("event_type", "is_recovered")
    search_fields = ("event_name", "event_id", "organizer_id")
    readonly_fields = ("is_active", "duration_seconds")
    ordering = ("-actual_start",)
    date_hierarchy = "actual_start"
    inlines = [EventAttendanceRecordInline]

    def is_active(self, obj):
        return obj.is_active
    is_active.boolean = True
    is_active.short_description = "Active"


@admin.register(EventAttendanceRecord)
class EventAttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("user_id", "event", "status", "join_time", "leave_time", "participation_score", "was_active_participant")
    list_filter = ("status", "was_active_participant", "was_muted", "was_deafened", "was_afk")
    search_fields = ("user_id", "event__event_name", "checkpoint_id")
    readonly_fields = ("participation_score", "was_active_participant")
    ordering = ("-join_time",)


# ---------------------------------------------------------------------------
# Nomination System
# ---------------------------------------------------------------------------

@admin.register(Nomination)
class NominationAdmin(admin.ModelAdmin):
    list_display = ("nominee_username", "nominator_username", "event_date", "status", "created_at", "processed_at")
    list_filter = ("status", "event_source")
    search_fields = ("nominee_username", "nominator_username", "reason", "denial_reason")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    fieldsets = (
        (None, {
            "fields": ("nominee_id", "nominee_username", "nominator_id", "nominator_username"),
        }),
        ("Event", {
            "fields": ("event_date", "event_id", "event_source"),
        }),
        ("Details", {
            "fields": ("reason", "status", "denial_reason"),
        }),
        ("Processing", {
            "fields": ("processed_by_id", "processed_by_username", "processed_at", "flags"),
        }),
    )


@admin.register(DistinctionLevel)
class DistinctionLevelAdmin(admin.ModelAdmin):
    list_display = ("level", "role_name", "nominations_required", "color")
    ordering = ("level",)
    search_fields = ("role_name",)


@admin.register(UserDistinction)
class UserDistinctionAdmin(admin.ModelAdmin):
    list_display = ("user_id", "current_level", "total_nominations", "total_recognitions", "current_streak", "nomination_rate")
    list_filter = ("current_level",)
    search_fields = ("user_id",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-total_nominations",)


@admin.register(EventReference)
class EventReferenceAdmin(admin.ModelAdmin):
    list_display = ("event_name", "source_system", "event_date", "organizer_username", "is_archived")
    list_filter = ("source_system", "is_archived")
    search_fields = ("event_name", "event_id", "organizer_username")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-event_date",)
    date_hierarchy = "event_date"


# ---------------------------------------------------------------------------
# Welcome System
# ---------------------------------------------------------------------------

@admin.register(UserJoinRecord)
class UserJoinRecordAdmin(admin.ModelAdmin):
    list_display = ("username", "global_name", "discord_id", "membership_status", "is_active", "join_count", "joined_at")
    list_filter = ("membership_status", "is_active")
    search_fields = ("username", "global_name", "discord_id")
    readonly_fields = ("joined_at", "last_seen", "last_updated")
    ordering = ("-joined_at",)
    date_hierarchy = "joined_at"
