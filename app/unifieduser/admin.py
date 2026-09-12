from contextlib import suppress

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.urls import path, reverse

from django.contrib.admin.sites import NotRegistered
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.admin import GroupAdmin

from ordered_model.admin import OrderedModelAdmin

from app.main.admin import GranularObjPermAdminMixin

from .models import OrgRank
from .forms import UnifiedUserCreationForm, UnifiedUserChangeForm


class ClonePermissionsForm(forms.Form):
    source_group = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        label="Copy all permissions from",
        help_text="Every permission in the selected group will be added to this group (existing permissions are kept).",
        required=True,
    )


class CustomGroupAdmin(GranularObjPermAdminMixin, GroupAdmin):

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:group_id>/clone-permissions/",
                self.admin_site.admin_view(self.clone_permissions_view),
                name="unifieduser_group_clone_permissions",
            ),
        ]
        return custom + urls

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["clone_form"] = ClonePermissionsForm()
        extra_context["clone_url"] = reverse(
            "admin:unifieduser_group_clone_permissions",
            args=[object_id],
        )
        return super().change_view(request, object_id, form_url, extra_context)

    def clone_permissions_view(self, request, group_id):
        target = Group.objects.get(pk=group_id)
        if request.method == "POST":
            form = ClonePermissionsForm(request.POST)
            if form.is_valid():
                source = form.cleaned_data["source_group"]
                existing = set(target.permissions.values_list("pk", flat=True))
                to_add = source.permissions.exclude(pk__in=existing)
                added = to_add.count()
                target.permissions.add(*to_add)
                messages.success(
                    request,
                    f'Copied {added} permission(s) from "{source.name}" → "{target.name}".',
                )
            else:
                messages.error(request, "Invalid form — no group selected.")
        return HttpResponseRedirect(
            reverse("admin:auth_group_change", args=[group_id])
        )


def refresh_display_names(modeladmin, request, queryset):
    from .models import DisplayNameSearchCache
    count = 0
    for user in queryset:
        cache.delete("uuser-dn-{0}".format(user.pk))
        DisplayNameSearchCache.objects.filter(user=user).delete()
        try:
            del user.__dict__["display_name"]
        except KeyError:
            pass
        user.display_name  # re-evaluate and re-cache
        count += 1
    messages.success(request, f"Refreshed display names for {count} user(s).")
refresh_display_names.short_description = "Refresh display name cache"


def repair_corrupted_user(modeladmin, request, queryset):
    """
    Fix OrgPlayer rows with a zero/null UUID pk by assigning a proper ULID
    and normalising the username to the discorduid.<uid> format.
    Only operates on rows where the pk is the nil UUID.
    """
    from django.db import connection
    from .models import ulid_new, DisplayNameSearchCache
    NIL_UUID = "00000000-0000-0000-0000-000000000000"
    fixed = 0
    skipped = 0
    for user in queryset:
        pk_str = str(user.pk)
        if pk_str != NIL_UUID:
            skipped += 1
            continue
        # Extract discord UID from username
        uid = None
        u = user.username or ""
        if u.startswith("discord_"):
            uid = u[len("discord_"):]
        elif u.startswith("discorduid."):
            uid = u[len("discorduid."):]

        new_pk = str(ulid_new())
        new_username = "discorduid.{0}".format(uid) if uid else u

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE unifieduser_orgplayer SET id=%s, username=%s WHERE id=%s",
                [new_pk, new_username, NIL_UUID],
            )
            cursor.execute(
                "UPDATE unifieduser_historicalorgplayer SET id=%s, username=%s WHERE id=%s",
                [new_pk, new_username, NIL_UUID],
            )
            # Update FK on DiscordUser if linked by old zero-pk user
            if uid:
                cursor.execute(
                    "UPDATE discordauth_discorduser SET user_id=%s WHERE user_id=%s",
                    [new_pk, NIL_UUID],
                )

        cache.delete("uuser-dn-{0}".format(NIL_UUID))
        DisplayNameSearchCache.objects.filter(user_id=NIL_UUID).delete()
        fixed += 1

    parts = []
    if fixed:
        parts.append(f"Repaired {fixed} corrupted user(s).")
    if skipped:
        parts.append(f"Skipped {skipped} user(s) (pk was not nil UUID).")
    messages.success(request, " ".join(parts) or "Nothing to do.")
repair_corrupted_user.short_description = "⚠️ Repair corrupted zero-UUID pk row"


def merge_duplicate_discord_users(modeladmin, request, queryset):
    from app.discordauth.management.commands.merge_duplicate_discord_users import (
        merge_duplicates_for_uid, _extract_uid,
    )
    total_merged = 0
    for user in queryset:
        uid = _extract_uid(user.username)
        if uid is None:
            continue
        _, count = merge_duplicates_for_uid(uid)
        total_merged += count
    if total_merged:
        messages.success(request, f"Merged {total_merged} duplicate OrgPlayer(s).")
    else:
        messages.info(request, "No duplicates found for the selected users.")
merge_duplicate_discord_users.short_description = "Merge duplicate OrgPlayers (same Discord UID)"


def merge_all_duplicate_discord_users(modeladmin, request, queryset):
    from app.discordauth.management.commands.merge_duplicate_discord_users import merge_all_duplicates
    total = merge_all_duplicates()
    if total:
        messages.success(request, f"Merged {total} duplicate OrgPlayer(s) across all users.")
    else:
        messages.info(request, "No duplicates found.")
merge_all_duplicate_discord_users.short_description = "⚠️ Merge ALL duplicate OrgPlayers (entire database)"


class UnifiedUserAdmin(UserAdmin):
    list_display = ('display_name', "username", "pretty_id", 'rank', "is_active", "is_staff", "is_superuser")
    search_fields = ['username'] # this is required for django's autocomplete functionality
    list_filter = ('rank', ) + UserAdmin.list_filter
    actions = [refresh_display_names, repair_corrupted_user, merge_duplicate_discord_users, merge_all_duplicate_discord_users]
#	ordering = ('display_name', ) + UserAdmin.ordering

    add_form = UnifiedUserCreationForm
    form = UnifiedUserChangeForm

    def display_name(self, obj):
        return obj.display_name
    display_name.short_description = 'Display Name'

    def pretty_id(self, obj):
        return obj.get_pretty_id()
    pretty_id.short_description = 'Pretty ID'
    pretty_id.admin_order_field = 'id'

    fieldsets = UserAdmin.fieldsets + (
        ('Organization Info', {
            'fields': ('rank',),
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Organization Info', {
            'fields': ('rank',),
        }),
    )


class RankAdmin(GranularObjPermAdminMixin, OrderedModelAdmin):
    list_display = ('name', 'prefix', 'move_up_down_links')
    search_fields = ('name', 'prefix')
    filter_horizontal = ('groups',)


with suppress(NotRegistered):
    admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), UnifiedUserAdmin)

with suppress(NotRegistered):
    admin.site.unregister(Group)
admin.site.register(Group, CustomGroupAdmin)

admin.site.register(OrgRank, RankAdmin)
