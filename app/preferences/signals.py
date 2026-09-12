import json

from django.dispatch import receiver
from django.db.models.signals import m2m_changed
from django.contrib.auth.models import Group
from django.core.exceptions import *
from django.contrib.auth import get_user_model
from django.db import transaction

from django_redis import get_redis_connection


PREFERENCES_PUBSUB_CHANNEL = "app.preferences.sync"


def publish(payload):
    r = get_redis_connection("default")
    r.publish(PREFERENCES_PUBSUB_CHANNEL, json.dumps(payload))


def pub_globalsetting_update(instance):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "GlobalSettingData.update",
        "type": "preferences.GlobalSetting",
        "id": str(instance.pk),
        "path": instance.for_setting.definition.import_path,
    })


def pub_globalsetting_delete(instance):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "GlobalSettingData.delete",
        "type": "preferences.GlobalSetting",
        "id": str(instance.pk),
        "path": instance.for_setting.definition.import_path,
    })


def pub_usersetting_update(instance):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "UserSettingData.update",
        "type": "preferences.UserSetting",
        "id": str(instance.pk),
        "path": instance.for_setting.definition.import_path,
        "user_id": str(instance.for_user.pk),
    })


def pub_usersetting_delete(instance):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "UserSettingData.delete",
        "type": "preferences.UserSetting",
        "id": str(instance.pk),
        "path": instance.for_setting.definition.import_path,
        "user_id": str(instance.for_user.pk),
    })
