from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe

from datetime import timedelta
from simple_history.admin import SimpleHistoryAdmin
from admin_auto_filters.filters import AutocompleteFilter

from app.main.admin import GranularObjPermAdmin, GranularObjPermAdminMixin

from .models import (
    LoggedDiscordChannel,
    LoggedDiscordMessage,
    LoggedVoiceStateSnapshot,
    LoggedVoiceState,
    CompiledLoggedVoiceState,
    UserVoiceProfile
)


# TODO: AI Gen'd verify/cleanup


class HasAttachmentsFilter(admin.SimpleListFilter):
    title = 'Has Attachments'
    parameter_name = 'has_attachments'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Yes'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            # Checks if the JSON list is not empty
            return queryset.exclude(attachments__exact=[])
        if self.value() == 'no':
            return queryset.filter(attachments__exact=[])
        return queryset


class BaseLogAdmin(object):
    """
    Base configuration to ensure both Live and History views 
    are read-only and display attachments correctly.
    """

    search_fields = ['author_name', 'author__user__username', 'content', 'id']

    list_filter = [
        'discord_executor',       # Dropdown for executor (handles None/Any automatically)
        HasAttachmentsFilter,     # Custom True/False for attachments
        'created_at',             # Django built-in date range sidebar
        'event',                  # Django built-in choice
        'moderated',              # Django built-in choice
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    readonly_fields = [
        'id', 'author', 'author_name', 'discord_executor', 
        'channel_id', 'content', 'created_at', 'updated_at', 
        'display_attachments_preview'
    ]

    fieldsets = (
        ('Meta', {'fields': ('id', 'created_at', 'channel_id')}),
        ('People', {'fields': ('author', 'author_name', 'discord_executor')}),
        ('Content', {'fields': ('content',)}),
        ('Media', {'fields': ('display_attachments_preview',)}),
    )

    def display_attachments_preview(self, obj):
        """
        Parses the JSON attachments and renders HTML for previews and links.
        Expects format: [["discord_url", "backup_url"], ...]
        """
        if not obj.attachments:
            return "No attachments"

        html_output = '<div style="display: flex; flex-wrap: wrap; gap: 15px;">'

        for i, urls in enumerate(obj.attachments):
            # Handle cases where inner list might not have 2 elements
            original = urls[0] if len(urls) > 0 else None
            backup = urls[1] if len(urls) > 1 else None

            # Use original for display, fallback to backup
            src = original or backup
            if not src: continue

            # Basic check to see if we should try to embed it as an image
            is_image = src.lower().split('?')[0].endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))

            html_output += f'''
            <div style="border: 1px solid #ddd; padding: 10px; border-radius: 8px; background: #f9f9f9; max-width: 300px;">
                <div style="margin-bottom: 8px; font-weight: bold; font-size: 12px; color: #555;">
                    Attachment {i+1}
                </div>
                {'<img src="{}" style="max-width: 100%; height: auto; border-radius: 4px; display: block; margin-bottom: 8px;">'.format(src) if is_image else ''}
                <div style="font-size: 11px;">
                    {f'<a href="{original}" target="_blank">Original Link</a>' if original else ''}
                    {' | ' if original and backup else ''}
                    {f'<a href="{backup}" target="_blank">Backup Link</a>' if backup else ''}
                </div>
            </div>
            '''

        html_output += '</div>'
        return mark_safe(html_output)  # TODO: XSS (unlikely)

    display_attachments_preview.short_description = "Attachments & Previews"


class GenericReadOnlyAdminMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoggedDiscordMessage)
class LoggedDiscordMessageAdmin(SimpleHistoryAdmin, BaseLogAdmin, GranularObjPermAdmin):
    # Columns for the list view
    list_display = ['id', "safe_author", 'author_name', 'short_content', 'attachment_count', 'created_at']
    readonly_fields = ('safe_author', 'safe_discord_executor')
    exclude = ('author', 'discord_executor')

    def short_content(self, obj):
        return (obj.content[:75] + '...') if len(obj.content) > 75 else obj.content
    short_content.short_description = "Message Content"

    def attachment_count(self, obj):
        return len(obj.attachments)
    attachment_count.short_description = "Files"


@admin.register(LoggedDiscordMessage.history.model)
class HistoricalLoggedDiscordMessageAdmin(BaseLogAdmin, admin.ModelAdmin):
    # We add history-specific columns like 'history_date'
    list_display = ['history_date', 'history_type', 'author_name', 'short_content']
    date_hierarchy = 'history_date'  # Adds a drill-down navigation bar for dates

    def short_content(self, obj):
        return (obj.content[:75] + '...') if len(obj.content) > 75 else obj.content
    short_content.short_description = "Content"


@admin.register(LoggedDiscordChannel)
class LoggedDiscordChannelAdmin(GenericReadOnlyAdminMixin, GranularObjPermAdminMixin, SimpleHistoryAdmin):
    list_display = ('id', 'name', 'channel_type', 'created_at', 'deleted_at')
    list_filter = ('channel_type',)
    search_fields = ('=id', 'name')
    date_hierarchy = 'created_at'


@admin.register(LoggedVoiceStateSnapshot)
class LoggedVoiceStateSnapshotAdmin(GenericReadOnlyAdminMixin, GranularObjPermAdmin):
    list_display = ('timestamp', 'expected_resolution', 'compiled')
    list_filter = ('compiled',)
    date_hierarchy = 'timestamp'


class DiscordUserFilter(AutocompleteFilter):
    title = 'Discord User'
    field_name = 'discorduser'


class ChannelFilter(AutocompleteFilter):
    title = 'Channel'
    field_name = 'channel'


@admin.register(LoggedVoiceState)
class LoggedVoiceStateAdmin(GenericReadOnlyAdminMixin, GranularObjPermAdmin):
    list_display = ('safe_discorduser', 'safe_channel', 'for_snapshot_timestamp', 'self_mute', 'self_deaf', 'self_stream', 'self_video')
    readonly_fields = ('safe_discorduser', 'safe_channel')
    exclude = ('discorduser', 'channel')
    list_filter = (DiscordUserFilter, ChannelFilter, 'self_mute', 'self_deaf', 'self_stream', 'self_video', 'server_mute', 'server_deaf')
    search_fields = ('=discorduser_id', 'discorduser__user__username')

    def for_snapshot_timestamp(self, obj):
        """Helper to display the snapshot timestamp cleanly in the list view"""
        return obj.for_snapshot.timestamp
    for_snapshot_timestamp.short_description = 'Snapshot Time'
    for_snapshot_timestamp.admin_order_field = 'for_snapshot__timestamp'


@admin.register(CompiledLoggedVoiceState)
class CompiledLoggedVoiceStateAdmin(GenericReadOnlyAdminMixin, GranularObjPermAdmin):
    list_display = ('safe_discorduser', 'safe_channel', 'interval_start_at', 'interval_stop_at', 'self_mute', 'self_deaf')
    readonly_fields = ('safe_discorduser', 'safe_channel')
    exclude = ('discorduser', 'channel')
    list_filter = (DiscordUserFilter, ChannelFilter, 'self_mute', 'self_deaf', 'self_stream', 'self_video', 'server_mute', 'server_deaf')
    search_fields = ('=discorduser_id', 'discorduser__user__username')
    date_hierarchy = 'interval_start_at'


@admin.register(UserVoiceProfile)
class UserVoiceProfileAdmin(GenericReadOnlyAdminMixin, GranularObjPermAdmin):
    # Adjust 'username' based on the actual fields available on your DiscordUser model
    list_display = ('pk', 'username_display', 'time_in_voice_30_days', 'view_detailed_logs')
    list_select_related = ('user',)
    search_fields = ('pk',)

    # We remove the default add/delete buttons to keep it as a pure reporting view
    actions = None 

    def username_display(self, obj):
        representation = obj.user.display_name if obj.user else obj.main_guild_nick or f"UID: {obj.pk}"
        return representation
    username_display.short_description = "Discord User"

    def time_in_voice_30_days(self, obj):
        """Uses the custom database router method to sum their time."""
        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)

        total_time = CompiledLoggedVoiceState.get_time_present(
            discorduser_id=obj.pk,
            start_time=thirty_days_ago,
            end_time=now
        )

        # Format the timedelta into a readable string (e.g., "45h 12m")
        total_seconds = int(total_time.total_seconds())
        if total_seconds == 0:
            return "0h 0m"

        hours, remainder = divmod(total_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours}h {minutes}m"

    time_in_voice_30_days.short_description = "Voice Time (Last 30 Days)"

    def view_detailed_logs(self, obj):
        """Creates a button to view this specific user's raw intervals."""
        app_label = CompiledLoggedVoiceState._meta.app_label
        url = reverse(f'admin:{app_label}_compiledloggedvoicestate_changelist') 

        # Append a query parameter to pre-filter the changelist by this user's ID
        url += f'?discorduser__id__exact={obj.pk}'

        return format_html('<a class="button" style="padding: 5px 10px; background: #417690; color: white; border-radius: 4px;" href="{}">View Raw Intervals</a>', url)

    view_detailed_logs.short_description = "Detailed Logs"
