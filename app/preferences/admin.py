from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django import forms
from django.template.defaultfilters import truncatechars
from django.core.exceptions import *

from app.main.admin import GranularObjPermAdmin

from .models import UserSetting, UserSettingData, GlobalSetting, GlobalSettingData


class ParentAppFilter(admin.SimpleListFilter):
    title = 'Parent App'
    parameter_name = 'parent_app'

    def lookups(self, request, model_admin):
        qs = model_admin.get_queryset(request)
        paths = qs.values_list('linked_module_class_path', flat=True)
        return [[y, y] for y in {x.rsplit(".", 2)[0] for x in paths}]

    def queryset(self, request, queryset):
        """ Return the filtered queryset """  # rip performance though
        if self.value():
            queryset = queryset.filter(linked_module_class_path__startswith=self.value())
        return queryset


class BaseSettingDataAdminForm(forms.ModelForm):
    def __init__(self, parent_object=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = self.fields['value'].initial

        try:
            definition = self.instance.for_setting.definition
        except (ObjectDoesNotExist, AttributeError):
            if parent_object != None:
                definition = parent_object.definition
                self.instance.for_setting = parent_object
            else:
                definition = None
                self.fields['value'].disabled = True

        if definition:
            widget_args = definition.get_widget_args(instance=self.instance, user=None, admin_editing=True)
            widget_args["initial"] = initial
            self.fields['value'] = definition.widget_class(**widget_args)
            self.fields['for_setting'].disabled = True


class GlobalSettingDataAdminForm(BaseSettingDataAdminForm):
    class Meta:
        model = GlobalSettingData
        fields = '__all__'


class GlobalSettingDataAdmin(admin.TabularInline):
    model = GlobalSettingData
    form = GlobalSettingDataAdminForm
    def get_formset(self, *args, **kwargs):
        FormSet = super().get_formset(*args, **kwargs)

        class ProxyFormSet(FormSet):
            def __init__(self, *args, **kwargs):
                form_kwargs = kwargs.pop('form_kwargs', {})
                form_kwargs['parent_object'] = kwargs['instance']
                super(ProxyFormSet, self).__init__(*args, form_kwargs=form_kwargs, **kwargs)

        return ProxyFormSet


class GlobalSettingAdmin(GranularObjPermAdmin):
    list_display = ('name', "value", "list_default_value",)
    readonly_fields = ("default_value", "linked_module_class_path",)
    list_filter = (ParentAppFilter,)
    inlines = [GlobalSettingDataAdmin]

    def list_default_value(self, obj):
        definition = obj.definition
        return truncatechars(getattr(definition, 'default_value', '—'), 100)

    def default_value(self, obj):
        definition = obj.definition
        return getattr(definition, 'default_value', None)

    def value(self, obj):
        choices = obj.definition.get_choices() if hasattr(obj.definition, "get_choices") else None
        if choices:
            try:
                result = {k:v for k,v in choices}[obj.data.value]
            except KeyError:
                result = "KeyError [{0}]".format(obj.data.value)
        else:
            result = obj.data.value
        return result
    value.admin_order_field = 'data__value'

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class UserViewableFilter(admin.SimpleListFilter):
    title = 'User Viewable'
    parameter_name = 'user_viewable'

    def lookups(self, request, model_admin):
        """ List of values to allow admin to select """
        return (
            ('True', 'Yes'),
            ('False', 'No'),
        )

    def queryset(self, request, queryset):
        """ Return the filtered queryset """ # rip performance though
        if self.value() == 'True':
            valid_objs = [obj.id for obj in queryset if obj.definition.user_viewable]
            return queryset.filter(pk__in=valid_objs)
        elif self.value() == 'False':
            valid_objs = [obj.id for obj in queryset if not obj.definition.user_viewable]
            return queryset.filter(pk__in=valid_objs)
        else:
            return queryset


class UserEditableFilter(admin.SimpleListFilter):
    title = 'User Editable'
    parameter_name = 'user_editable'

    def lookups(self, request, model_admin):
        """ List of values to allow admin to select """
        return (
            ('True', 'Yes'),
            ('False', 'No'),
        )

    def queryset(self, request, queryset):
        """ Return the filtered queryset """ # rip performance though
        if self.value() == 'True':
            valid_objs = [obj.id for obj in queryset if obj.definition.user_editable]
            return queryset.filter(pk__in=valid_objs)
        elif self.value() == 'False':
            valid_objs = [obj.id for obj in queryset if not obj.definition.user_editable]
            return queryset.filter(pk__in=valid_objs)
        else:
            return queryset


class UserSettingDataAdminForm(BaseSettingDataAdminForm):
    class Meta:
        model = UserSettingData
        fields = '__all__'


class UserSettingDataAdmin(admin.TabularInline):
    model = UserSettingData
    form = UserSettingDataAdminForm
    extra = 0
    list_filter = ("for_setting", "for_user",)
    autocomplete_fields = ["for_user"]

    def get_formset(self, *args, **kwargs):
        FormSet = super().get_formset(*args, **kwargs)

        class ProxyFormSet(FormSet):
            def __init__(self, *args, **kwargs):
                form_kwargs = kwargs.pop('form_kwargs', {})
                form_kwargs['parent_object'] = kwargs['instance']
                super(ProxyFormSet, self).__init__(*args, form_kwargs=form_kwargs, **kwargs)

        return ProxyFormSet


class UserSettingAdmin(GranularObjPermAdmin):
    list_display = ('__str__', "default_value", "user_viewable", "user_editable",)
    readonly_fields = ("default_value", "user_viewable", "user_editable", "linked_module_class_path",)
    list_filter = (UserViewableFilter, UserEditableFilter, ParentAppFilter)
    inlines = [UserSettingDataAdmin]

    def default_value(self, obj):
        definition = obj.definition
        return definition.default_value

    def user_viewable(self, obj):
        return obj.definition.user_viewable
    user_viewable.boolean = True

    def user_editable(self, obj):
        return obj.definition.user_editable
    user_editable.boolean = True

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


#admin.site.register(UserSettingData, UserSettingDataAdmin)
admin.site.register(UserSetting, UserSettingAdmin)
admin.site.register(GlobalSetting, GlobalSettingAdmin)
