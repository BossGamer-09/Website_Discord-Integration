from django import forms
from django.apps import apps
from django.core.cache import cache

from app.preferences.utils import user_preference, global_preference
from app.preferences.types import BoolChoicePreferenceDefinition, IntChoicePreferenceDefinition, CharPreferenceDefinition, IntMultipleChoicePreferenceDefinition


@user_preference
class UserRequiresMFA(BoolChoicePreferenceDefinition):
    initial_name = "Require MFA on Login"
    initial_description = "Even when the app does not require MFA the user will always be required to login with MFA, if no MFA is currently setup on login the user will be required to set it up."
    default_value = False
    user_viewable = True
    user_editable = True

    choices = (
        (False, 'No'),
        (True, 'Yes'),
    )


@user_preference
class DisplayNameSource(IntChoicePreferenceDefinition):
    initial_name = "Preferred Display Name Source"
    initial_description = "Automatic chooses based on preset algo"
    default_value = 0
    user_viewable = True
    user_editable = False

    choices = (
        (0, 'automatic'),
        (1, 'Discord username'),
        (2, 'Spectrum'),
        (3, 'ORG Discord nickname'),
        (4, 'Rank + Custom Defined Username'),
    )

    @classmethod
    def on_db_update(cls, obj):
        cache.delete("uuser-dn-{0}".format(obj.for_user.id))
        super().on_db_update(obj)

    @classmethod
    def on_db_delete(cls, obj):
        cache.delete("uuser-dn-{0}".format(obj.for_user.id))
        super().on_db_delete(obj)


@user_preference
class AvatarSource(IntChoicePreferenceDefinition):
    initial_name = "Preferred Avatar Source"
    initial_description = ""
    default_value = 0
    user_viewable = True
    user_editable = True

    choices = (
        (0, 'automatic'),
        (1, 'from Discord'),
        (2, 'Spectrum'),
    )


@user_preference
class CustomDisplayName(CharPreferenceDefinition):
    initial_name = "Custom Defined Username"
    initial_description = "Custom Defined Username to use when DisplayNameSource == Custom Defined Username"
    default_value = ""

    user_viewable = True
    user_editable = False

    @classmethod
    def on_db_update(cls, obj):
        UserSetting = apps.get_model(app_label='preferences', model_name='UserSetting')

        if UserSetting.get_setting_value(DisplayNameSource.import_path, obj.for_user) == 4:
            cache.delete("uuser-dn-{0}".format(obj.for_user.id))
            pass

        #signal generic pref pubsub to discord for username update
        super().on_db_update(obj)


    @classmethod
    def on_db_delete(cls, obj):
        UserSetting = apps.get_model(app_label='preferences', model_name='UserSetting')

        if UserSetting.get_setting_value(DisplayNameSource.import_path, obj.for_user) == 4:
            raise NotImplementedError  # Stop it from deleting

        # TODO: probably should not be allow for our discord integration, well minus when user actually deleted, for now dont guard, as its admin only, technically empty name doesnt raise issues, just looks odd
        #signal generic pref pubsub to discord for username update
        super().on_db_delete(obj)
