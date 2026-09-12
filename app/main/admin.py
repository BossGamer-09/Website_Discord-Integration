from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth import get_permission_codename
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import ObjectDoesNotExist
from django import forms

from guardian.utils import get_group_obj_perms_model, get_user_obj_perms_model
from guardian.shortcuts import get_objects_for_user

from guardian.admin import GuardedModelAdminMixin


class GroupModelManage(forms.Form):
    group = forms.ModelChoiceField(queryset=Group.objects.all(), widget=AutocompleteSelect(get_group_obj_perms_model()._meta.get_field("group"), admin.site))

    def clean_group(self):
        try:
            return self.cleaned_data['group']
        except Group.DoesNotExist:
            raise forms.ValidationError(self.fields['group'].error_messages['does_not_exist'])


class UserModelManage(forms.Form):
    user = forms.ModelChoiceField(queryset=get_user_model().objects.all(), widget=AutocompleteSelect(get_user_obj_perms_model()._meta.get_field("user"), admin.site))

    def clean_user(self):
        try:
            return self.cleaned_data['user']
        except ObjectDoesNotExist:
            raise forms.ValidationError(self.fields['user'].error_messages['does_not_exist'])


class AutoCompleteObjPermAdminMixin:
    class Media:
        js = (
            "admin/js/vendor/jquery/jquery.js",
            "admin/js/vendor/select2/select2.full.js",
            "admin/js/jquery.init.js",
            "admin/js/autocomplete.js",
        )
        css = {
            "screen": [
                "admin/css/vendor/select2/select2.css",
                "admin/css/autocomplete.css",
            ]
        }

    def get_obj_perms_user_select_form(self, request):
        return UserModelManage

    def get_obj_perms_group_select_form(self, request):
        return GroupModelManage


class GranularGuardedModelAdminMixin(GuardedModelAdminMixin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Check global permission
        if super().has_change_permission(request) or (not self.list_editable and self.has_view_permission(request)):
            return qs
        # No global, filter by row-level permissions. also use view permission if the changelist is not editable
        if self.list_editable:
            return get_objects_for_user(request.user, [get_permission_codename('change', self.opts)], qs)
        else:
            return get_objects_for_user(request.user, [get_permission_codename('change', self.opts), self.get_view_permission()], qs, any_perm=True)

    def has_change_permission(self, request, obj=None):
        if super().has_change_permission(request, obj):
            return True
        if obj is None:
            # Here check global 'view' permission or if there is any changeable items
            return self.has_view_permission(request) or self.get_queryset(request).exists()
        else:
            # Row-level checking
            return request.user.has_perm(self.opts.get_change_permission(), obj)

    def get_view_permission(self):
        return 'view_{}'.format(self.opts.object_name.lower())

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("{}.{}".format(self.opts.app_label, self.get_view_permission()), obj)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) or (obj is not None and request.user.has_perm(self.opts.get_delete_permission(), obj))


class GranularObjPermAdminMixin(AutoCompleteObjPermAdminMixin, GranularGuardedModelAdminMixin):
    pass


class GranularObjPermAdmin(GranularObjPermAdminMixin, admin.ModelAdmin):
    pass
