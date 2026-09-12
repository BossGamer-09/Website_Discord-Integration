from contextlib import suppress

from django import forms
from django.contrib import admin
from django.apps import apps
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.contrib.auth.models import Group
from django.db.models import Exists, OuterRef
from django.utils.html import format_html

from ordered_model.admin import OrderedTabularInline, OrderedStackedInline, OrderedInlineModelAdminMixin, OrderedModelAdmin
from guardian.shortcuts import get_objects_for_user
from simple_history.admin import SimpleHistoryAdmin

from .models import DiscordRole, GroupDiscordRole, DiscordUserGuildEvent, OrgPlayerNote

from app.main.util.admin import GuardianGenericAutocompleteMixin
from app.unifieduser.admin import UnifiedUserAdmin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from app.main.admin import GranularObjPermAdmin, GranularObjPermAdminMixin


class HighPerformanceGroupDiscordRoleFilter(admin.SimpleListFilter):
    title = 'Permission Groups'
    parameter_name = 'group_id'

    def lookups(self, request, model_admin):
        options = [
            ('any', 'Any'),
            ('none', 'None'),
        ]

        group_has_discordrole = GroupDiscordRole.objects.filter(group=OuterRef('pk'))
        groups = Group.objects.annotate(has_discordrole=Exists(group_has_discordrole)).filter(has_discordrole=True).values_list('pk', 'name')

        for pk, name in groups:
            options.append((str(pk), name))

        return options

    def queryset(self, request, queryset):
        value = self.value()

        role_exists = GroupDiscordRole.objects.filter(
            discord_role=OuterRef('pk')
        )

        if value == 'none':
            return queryset.filter(~Exists(role_exists))

        if value == 'any':
            return queryset.filter(Exists(role_exists))

        if value:
            specific_group_exists = GroupDiscordRole.objects.filter(discord_role=OuterRef('pk'), group_id=value)
            return queryset.filter(Exists(specific_group_exists))

        return queryset

class GroupDiscordRoleInline(admin.TabularInline):
    model = Group.discord_roles.through


class DiscordRoleAdmin(GranularObjPermAdminMixin, OrderedModelAdmin):
    list_display = ("name", "pretty", "role_id")
    list_filter = (HighPerformanceGroupDiscordRoleFilter,)
    readonly_fields = ("discord_order", "emoji_img", "emoji_code", "color", )  # "temp_for_event"
    ordering = ('-discord_order',)
#    filter_horizontal = ('permission_groups',)
    inlines = [
        GroupDiscordRoleInline,
    ]
    exclude = ('permission_groups',)

    def name(self, obj):
        return obj.name or "None [{}]".format(obj.id)
    name.short_description = "Name"

    def color(self, obj):
        return obj.color
    color.short_description = "Color"

    def pretty(self, obj):
        return format_html('<span style="{}font-weight: bold;">{}{}</span>', format_html('color: rgb({}) !important; ', ",".join(str(x) for x in obj.color_rgb)) if obj.color_rgb else "", format_html('<img src="{}"></img>', obj.emoji_url) if obj.emoji_url else "", obj.name)
    pretty.short_description = "Pretty"

    def emoji_img(self, obj):
        if obj.emoji_url:
            return format_html('<img src="{}"></img>', obj.emoji_url)
        else:
            return ''
    emoji_img.short_description = "Emoji"

    def emoji_code(self, obj):
        return obj.emoji_code
    emoji_code.short_description = "Emoji Code"

    def get_readonly_fields(self, request, obj=None):
        if obj: # editing an existing object
            # All model fields as read_only
            return self.readonly_fields + ('role_id',)
        return self.readonly_fields

#    def has_delete_permission(self, request, obj=None):
#        return False

#    def has_add_permission(self, request, obj=None):
#        return False


class DiscordRoleInlineAdmin(OrderedTabularInline):
    model = DiscordRole
    fields = ('role_id', "name", 'move_up_down_links',)
    readonly_fields = ("name", 'move_up_down_links',)
    ordering = ('order',)
    extra = 1

    def name(self, obj):
        return obj.name
    name.short_description = "Name"

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(DiscordRole, DiscordRoleAdmin)


class DynamicRelatedObjectFilter(admin.SimpleListFilter):
    title = 'Related Object'
    parameter_name = 'object_id'

    def lookups(self, request, model_admin):
        # Check if the user has already filtered by a Content Type in the sidebar
        content_type_id = request.GET.get('content_type__id__exact')
        
        if not content_type_id:
            # If no model is selected, return an empty list so we don't try 
            # to render every object from every table in the database!
            return [] 
            
        try:
            content_type = ContentType.objects.get(pk=content_type_id)
            model_class = content_type.model_class()
        except ContentType.DoesNotExist:
            return []

        # HIGH PERFORMANCE: Find only the exact object_ids that have notes.
        # .distinct() ensures that if an order has 50 notes, it only appears once in the sidebar.
        used_object_ids = model_admin.model.objects.filter(
            content_type_id=content_type_id
        ).values_list('object_id', flat=True).distinct()

        # Fetch the actual objects using those IDs so the sidebar shows pretty names.
        # Cap it at 250 to ensure the sidebar never freezes the browser.
        target_objects = model_class.objects.filter(pk__in=used_object_ids)[:250]

        return [(str(obj.pk), str(obj)) for obj in target_objects]

    def queryset(self, request, queryset):
        # Apply the filter when an object is clicked
        if self.value():
            return queryset.filter(object_id=self.value())
        return queryset


@admin.register(OrgPlayerNote)
class UserNoteAdmin(GuardianGenericAutocompleteMixin, admin.ModelAdmin):
    list_display = ['user', 'related_object_link', 'date']
    fields = ['user', 'message', 'content_type', 'object_id', 'related_object_link']
    readonly_fields = ['related_object_link']

    list_select_related = ['user', 'content_type']

    list_filter = [
        ('content_type', admin.RelatedOnlyFieldListFilter),
        ('user', admin.RelatedOnlyFieldListFilter),
        DynamicRelatedObjectFilter,
    ]
