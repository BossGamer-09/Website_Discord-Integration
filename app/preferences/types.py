import jsonschema
import json

from django import forms
from django.core.exceptions import *
from django.apps import apps

from .validators import SettingValidatorHelper
from django_admin_json_editor import JSONEditorWidget as SchemaJSONEditorWidget
from django.forms import JSONField


class BasePreferenceDefinition(object):
    import_path: str  # set dynamically by global_preference/user_preference decorator

    @classmethod
    def get_widget_args(cls, instance, user=None, admin_editing=False):
        raise NotImplementedError

    @classmethod
    def validator(cls, value, instance, user=None, admin_editing=False):
        return value

    @classmethod
    def on_db_update(cls, obj):
        GlobalSettingData = apps.get_model(app_label='preferences', model_name='GlobalSettingData')
        UserSettingData = apps.get_model(app_label='preferences', model_name='UserSettingData')

        if isinstance(obj, GlobalSettingData):
            from .signals import pub_globalsetting_update
            pub_globalsetting_update(obj)
        elif isinstance(obj, UserSettingData):
            from .signals import pub_usersetting_update
            pub_usersetting_update(obj)
        else:
            raise NotImplementedError

    @classmethod
    def on_db_delete(cls, obj):
        GlobalSettingData = apps.get_model(app_label='preferences', model_name='GlobalSettingData')
        UserSettingData = apps.get_model(app_label='preferences', model_name='UserSettingData')

        if isinstance(obj, GlobalSettingData):
            from .signals import pub_globalsetting_delete
            pub_globalsetting_delete(obj)
        elif isinstance(obj, UserSettingData):
            from .signals import pub_usersetting_delete
            pub_usersetting_delete(obj)
        else:
            raise NotImplementedError



class JSONPreferenceDefinition(BasePreferenceDefinition):
    widget_class = JSONField
    initial_name = NotImplemented
    default_value = None

    @classmethod
    def get_widget_args(cls, instance, user=None, admin_editing=False):
        return {"label":instance.for_setting.name or cls.initial_name, "initial":instance.value or cls.default_value, "widget":forms.TextInput(), "required":True, "validators":[SettingValidatorHelper(cls.validator, instance, user, admin_editing),]}

    @classmethod
    def validator(cls, value, instance, user=None, admin_editing=False):
        return value


class SchemaPreferenceDefinition(BasePreferenceDefinition):
    widget_class = JSONField
    client_schema = {}
    server_schema = {}
    initial_name = NotImplemented
    default_value = NotImplemented

    @classmethod
    def validator(cls, value, instance, user=None, admin_editing=False):
        try: 
            jsonschema.validate(value, cls.server_schema) 
        except jsonschema.exceptions.ValidationError as e: 
            raise ValidationError("{}: {}".format(repr(e), str(e)), code="invalid", params={})
        except jsonschema.exceptions.SchemaError as e: 
            raise ValidationError("{}: {}".format(repr(e), str(e)), code="invalid", params={})
        except Exception as e:
            raise ValidationError("{}".format(str(e)), code="invalid", params={})
        else:
            return value

    @classmethod
    def get_widget_args(cls, instance, user=None, admin_editing=False):
        return {
            "label": instance.for_setting.name or cls.initial_name,
            "initial": instance.value or cls.default_value,
            "widget": SchemaJSONEditorWidget(schema=cls.client_schema, collapsed=True, independent_fieldset=True, editor_options={"keep_oneof_values": False}),
            "required": True,
            "validators": [SettingValidatorHelper(cls.validator, instance, user, admin_editing),]
        }

class BaseChoicePreferenceDefinition(BasePreferenceDefinition):
    widget_class = forms.TypedChoiceField
    choices = []
    initial_name = NotImplemented
    default_value = NotImplemented
    choices_coercer = NotImplemented

    @classmethod
    def validator(cls, value, instance, user=None, admin_editing=False):
        return value

    @classmethod
    def get_choices(cls):
        return cls.choices

    @classmethod
    def get_widget_args(cls, instance, user=None, admin_editing=False):
        return {
            "choices": cls.get_choices(),
            "label": instance.for_setting.name or cls.initial_name,
            "coerce": cls.choices_coercer,
            "initial": str(instance.value or cls.default_value),
            "widget": forms.Select(),
            "required": True,
            "validators": [SettingValidatorHelper(cls.validator, instance, user, admin_editing),]
        }

class BoolChoicePreferenceDefinition(BaseChoicePreferenceDefinition):
    choices = [(True, True),(False, False)]
    choices_coercer = bool

class IntChoicePreferenceDefinition(BaseChoicePreferenceDefinition):
    choices_coercer = int

class StrChoicePreferenceDefinition(BaseChoicePreferenceDefinition):
    choices_coercer = str

class BaseMultipleChoicePreferenceDefinition(BasePreferenceDefinition):
    widget_class = forms.TypedMultipleChoiceField
    choices = []
    initial_name = NotImplemented
    default_value = NotImplemented
    choices_coercer = NotImplemented
    _is_multiple = True

    @classmethod
    def get_choices(cls, instance, user):
        return cls.choices

    @classmethod
    def get_widget_args(cls, instance, user=None, admin_editing=False):
        return {
            "choices": cls.get_choices(instance, user),
            "label": instance.for_setting.name or cls.initial_name,
            "coerce": cls.choices_coercer,
            "initial": [str(x) for x in (instance.value or cls.default_value)],
            "widget": forms.SelectMultiple(),
            "required": True,
            "validators": [SettingValidatorHelper(cls.validator, instance, user, admin_editing),]
        }

    @classmethod
    def validator(cls, value, instance, user=None, admin_editing=False):
        return value


class BoolMultipleChoicePreferenceDefinition(BaseMultipleChoicePreferenceDefinition):
    choices = [(True, True),(False, False)]
    choices_coercer = bool

class IntMultipleChoicePreferenceDefinition(BaseMultipleChoicePreferenceDefinition):
    choices_coercer = int

class StrMultipleChoicePreferenceDefinition(BaseMultipleChoicePreferenceDefinition):
    choices_coercer = str


class IntPreferenceDefinition(BasePreferenceDefinition):
    widget_class = forms.IntegerField

    @classmethod
    def get_widget_args(cls, instance, user=None, admin_editing=False):
        return {"label":instance.for_setting.name or cls.initial_name, "initial":instance.value or cls.default_value, "widget":forms.NumberInput(), "required":True, "validators":[SettingValidatorHelper(cls.validator, instance, user, admin_editing),]}

    @classmethod
    def validator(cls, value, instance, user=None, admin_editing=False):
        return value

class CharPreferenceDefinition(BasePreferenceDefinition):
    widget_class = forms.CharField

    @classmethod
    def get_widget_args(cls, instance, user=None, admin_editing=False):
        return {"label":instance.for_setting.name or cls.initial_name, "initial":instance.value or cls.default_value, "widget":forms.TextInput(), "required":True, "validators":[SettingValidatorHelper(cls.validator, instance, user, admin_editing),]}

    @classmethod
    def validator(cls, value, instance, user=None, admin_editing=False):
        return value

class TextPreferenceDefinition(BasePreferenceDefinition):
    widget_class = forms.CharField

    @classmethod
    def get_widget_args(cls, instance, user=None, admin_editing=False):
        return {"label":instance.for_setting.name or cls.initial_name, "initial":instance.value or cls.default_value, "widget":forms.Textarea(), "required":True, "validators":[SettingValidatorHelper(cls.validator, instance, user, admin_editing),]}

    @classmethod
    def validator(cls, value, instance, user=None, admin_editing=False):
        return value


class PermissionGroupSelectPreferenceDefinition(IntChoicePreferenceDefinition):
    default_value = None

    @classmethod
    def get_choices(cls):
        Group = apps.get_model('auth', 'Group')
        return [[group.pk, group.name] for group in Group.objects.all()]

class PermissionGroupMultiSelectPreferenceDefinition(IntMultipleChoicePreferenceDefinition):
    default_value = None

    @classmethod
    def get_choices(cls):
        Group = apps.get_model('auth', 'Group')
        return [[group.pk, group.name] for group in Group.objects.all()]


class DiscordRoleSelectPreferenceDefinition(IntChoicePreferenceDefinition):
    default_value = None

    @classmethod
    def get_choices(cls):
        DiscordRole = apps.get_model('org', 'DiscordRole')
        return [[discordrole.pk, discordrole.name or f"{discordrole.role_id} [ID]"] for discordrole in DiscordRole.objects.all().order_by("discord_order")]

class DiscordRoleMultiSelectPreferenceDefinition(IntMultipleChoicePreferenceDefinition):
    default_value = None

    @classmethod
    def get_choices(cls):
        DiscordRole = apps.get_model('org', 'DiscordRole')
        return [[discordrole.pk, discordrole.name or f"{discordrole.role_id} [ID]"] for discordrole in DiscordRole.objects.all().order_by("discord_order")]


class DiscordChannelSelectPreferenceDefinition(IntChoicePreferenceDefinition):
    default_value = None
    # discovered types: channel_types_filter = ["category", "forum", "private_thread", "public_thread", "text", "voice"]
    channel_types_filter = ["text", "voice"]
    not_deleted = True

    @classmethod
    def get_choices(cls):
        LoggedDiscordChannel = apps.get_model('discordlogger', 'LoggedDiscordChannel')
        qs = LoggedDiscordChannel.objects.filter(channel_type__in=cls.channel_types_filter).only('pk', 'name').order_by("parent", "-position")

        if cls.channel_types_filter:
            qs = qs.filter(channel_type__in=cls.channel_types_filter)

        if cls.not_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        if len(cls.channel_types_filter) == 1:
            return [[discordchannel.pk, discordchannel.name] for discordchannel in qs]
        else:
            return [[discordchannel.pk, f"{discordchannel.name} [{discordchannel.channel_type}]"] for discordchannel in qs]


class DiscordChannelMultiSelectPreferenceDefinition(IntMultipleChoicePreferenceDefinition):
    default_value = None
    # discovered types: channel_types_filter = ["category", "forum", "private_thread", "public_thread", "text", "voice"]
    channel_types_filter = ["text", "voice"]
    not_deleted = True

    @classmethod
    def get_choices(cls):
        LoggedDiscordChannel = apps.get_model('discordlogger', 'LoggedDiscordChannel')
        qs = LoggedDiscordChannel.objects.filter(channel_type__in=cls.channel_types_filter).only('pk', 'name').order_by("parent", "-position")

        if cls.channel_types_filter:
            qs = qs.filter(channel_type__in=cls.channel_types_filter)

        if cls.not_deleted:
            qs = qs.filter(deleted_at__isnull=True)

        if len(cls.channel_types_filter) == 1:
            return [[discordchannel.pk, discordchannel.name] for discordchannel in qs]
        else:
            return [[discordchannel.pk, f"{discordchannel.name} [{discordchannel.channel_type}]"] for discordchannel in qs]
