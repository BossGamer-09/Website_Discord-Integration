import json

from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.core.exceptions import *

from pygments import highlight
from pygments.lexers import JsonLexer
from pygments.formatters import HtmlFormatter

from app.main.admin import GranularObjPermAdmin

from .models import DiscordUser, DiscordDevice


class DiscordUserAdmin(GranularObjPermAdmin):
    readonly_fields = ('first_seen_at', 'avatar_img', 'full_username', "raw_user_info")
    search_fields = ['user__username', "discorduid"]  # this is required for django's autocomplete functionality
    autocomplete_fields = ['user']

    actions = ["sync_discord_roles_linked_groups"]

    @admin.action(description="Force Sync Discord Data and Roles from API")
    def sync_discord_roles_linked_groups(self, request, queryset):
        for discorduser in queryset.all():
            discorduser.task_sync_discord_roles_linked_groups(force_refresh_cache=True)

    def avatar_img(self, obj):
        if obj.avatar_url:
            return format_html('<img src="{}"></img>', obj.avatar_url)
        else:
            return ''
    avatar_img.short_description = 'Avatar image'

    def raw_user_info(self, obj):
        response = json.dumps(obj.user_info, sort_keys=True, indent=4)
        formatter = HtmlFormatter(style='stata-dark')
        response = highlight(response, JsonLexer(), formatter)
        style = "<style>" + formatter.get_style_defs() + "</style>"
        return mark_safe(style + response)
    raw_user_info.short_description = 'Cached OAuth user info'


class DiscordDeviceAdmin(GranularObjPermAdmin):
    pass


admin.site.register(DiscordUser, DiscordUserAdmin)
admin.site.register(DiscordDevice, DiscordDeviceAdmin)
