from django.core.cache import cache
from django.utils.module_loading import import_string
from django.utils.module_loading import autodiscover_modules
from django import forms
from django.core.exceptions import *

from django.apps import apps

from asgiref.sync import sync_to_async, async_to_sync
from channels.db import database_sync_to_async


def user_preference(obj):  # decorator
    user_preferences_manager.register(obj)
    return obj


def global_preference(obj):  # decorator
    global_preferences_manager.register(obj)
    return obj


def get_user_preference(import_path, user):
    value = cache.get("usersetting-{0}-{1}".format(import_path, user.id))
    if value is None:
        UserSetting = apps.get_model(app_label='preferences', model_name='UserSetting')
        value = UserSetting.get_setting_value(setting=import_path, user=user)
    return value


async def aget_user_preference(import_path, user):
    return await database_sync_to_async(get_user_preference)(import_path, user)


def get_global_preference(import_path):
    value = cache.get("globalsetting-{0}".format(import_path))
    if value is None:
        GlobalSetting = apps.get_model(app_label='preferences', model_name='GlobalSetting')
        value = GlobalSetting.get_setting_value(setting=import_path)
    return value


async def aget_global_preference(import_path):
    return await database_sync_to_async(get_global_preference)(import_path)


class BasePreferencesManager(object):
    model_name = None

    def __init__(self):
        self.pending = []

    def on_ready(self):
        self.registry = {}
        self.register = self._register
        for obj in self.pending:
            self.register(obj)

    def register(self, obj):
        self.pending.append(obj)

    def _register(self, obj):
        import_path = '{}.{}'.format(obj.__module__, obj.__qualname__)
        obj.import_path = import_path
        self.registry[import_path] = obj

    def populate_db(self, remove_orphans=False):
        model = apps.get_model(app_label='preferences', model_name=self.model_name)

        for import_path, obj in self.registry.items():
            _, created = model.objects.get_or_create(linked_module_class_path=import_path, defaults={'description': obj.initial_description, 'name': obj.initial_name})
            if created:
                print("{0} Created".format(self.model_name), self.__class__, import_path)

        if remove_orphans:
            valid_import_paths = list(self.registry.keys())
            
            deleted_count, _ = model.objects.exclude(linked_module_class_path__in=valid_import_paths).delete()
            
            if deleted_count:
                print("{0} Removed {1} orphaned entries".format(self.model_name, deleted_count), self.__class__)

    def get_object(self, import_path):
        return self.registry[import_path]


class GlobalPreferencesManager(BasePreferencesManager):
    model_name = 'GlobalSetting'


class UserPreferencesManager(BasePreferencesManager):
    model_name = 'UserSetting'


global_preferences_manager = GlobalPreferencesManager()
user_preferences_manager = UserPreferencesManager()

all_managers = [global_preferences_manager, user_preferences_manager]


def autodiscover():
    autodiscover_modules('preferences')
