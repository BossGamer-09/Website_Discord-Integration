from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import models
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import JSONField
from django.core.exceptions import *
from django.db.models import Q

from asgiref.sync import sync_to_async

from .utils import global_preferences_manager, user_preferences_manager
from app.main.util.db import RestrictedQuerySet


class GlobalSetting(models.Model):
    linked_module_class_path = models.CharField(max_length=512, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=1000, blank=True, null=True)

    @property
    def definition(self):
        return global_preferences_manager.get_object(self.linked_module_class_path)

    def __str__(self):
        return "{} {}".format(self.name, self.linked_module_class_path) if self.name else self.linked_module_class_path

    @classmethod
    def get_setting_value(cls, setting):
        value = cache.get("globalsetting-{0}".format(setting))
        if value is None:
            try:
                value = GlobalSettingData.objects.get(for_setting__linked_module_class_path=setting).value
            except GlobalSettingData.DoesNotExist:
                try:
                    value = cls.objects.get(linked_module_class_path=setting).definition.default_value
                except cls.DoesNotExist:
                    # Preference not yet registered via populate_db — return None so callers
                    # treat it as "not configured" rather than crashing.
                    return None
            cache.set("globalsetting-{}".format(setting), value, 60*60*24*2)
        return value

    @classmethod
    def get_setting_obj(cls, setting):
        try:
            data = GlobalSettingData.objects.get(for_setting__linked_module_class_path=setting)
        except GlobalSettingData.DoesNotExist:
            setting = cls.objects.get(linked_module_class_path=setting)
            data = GlobalSettingData(for_setting=setting, value=setting.definition.default_value)
        return data

    class Meta:
        indexes = [
            models.Index(fields=['linked_module_class_path'], name='preferences_gs_lmcp_vpo', opclasses=["varchar_pattern_ops"]),
        ]


class GlobalSettingDataQuerySet(RestrictedQuerySet):
    def delete(self, *args, **kwargs):
        for obj in self:
            self.for_setting.definition.on_db_delete(self)
            cache.delete("globalsetting-{}".format(obj.for_setting.linked_module_class_path))
        return models.QuerySet.delete(*args, **kwargs)


class GlobalSettingData(models.Model):
    objects = GlobalSettingDataQuerySet.as_manager()

    value = JSONField()
    for_setting = models.OneToOneField(GlobalSetting, related_name='data', on_delete=models.CASCADE)

    def save(self, *args, **kwargs):
        self.for_setting.definition.on_db_update(self)
        rtn = super().save(*args, **kwargs)
        cache.set("globalsetting-{}".format(self.for_setting.linked_module_class_path), self.value, 60*60*24*2) # 2 days
        return rtn

    def delete(self, *args, **kwargs):
        self.for_setting.definition.on_db_delete(self)
        cache.delete("globalsetting-{}".format(self.for_setting.linked_module_class_path))
        return super().delete(*args, **kwargs)

    def __str__(self):
        return "{}".format(self.for_setting)


class UserSetting(models.Model):
    linked_module_class_path = models.CharField(max_length=512, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=1000, blank=True, null=True)

    class Meta(object):
        permissions = (
            ("can_usersetting_view_own", "Can view own preferences"),
            ("can_usersetting_edit_own", "Can edit own preferences"),
        )

    @property
    def definition(self):
        return user_preferences_manager.get_object(self.linked_module_class_path)

    def __str__(self):
        return self.linked_module_class_path

    @classmethod
    def get_setting_value(cls, setting, user):
        value = cache.get("usersetting-{0}-{1}".format(setting, user.id))
        if value is None:
            try:
                value = UserSettingData.objects.get(for_setting__linked_module_class_path=setting, for_user=user).value
            except UserSettingData.DoesNotExist:
                value = cls.objects.get(linked_module_class_path=setting).definition.default_value
            cache.set("usersetting-{0}-{1}".format(setting, user.id), value, 60*60*24*2)
        return value

    @classmethod
    async def aget_setting_value(cls, setting, user):
        value = await sync_to_async(cls.get_setting_value)(setting, user)
        return value

    @classmethod
    def get_setting_obj(cls, setting, user):
        try:
            data = UserSettingData.objects.get(for_setting__linked_module_class_path=setting, for_user=user)
        except UserSettingData.DoesNotExist:
            setting = cls.objects.get(linked_module_class_path=setting)
            data = UserSettingData(for_setting=setting, for_user=user, value=setting.definition.default_value)
        return data

    @classmethod
    async def aget_setting_obj(cls, setting, user):
        try:
            data = await UserSettingData.objects.aget(for_setting__linked_module_class_path=setting, for_user=user)
        except UserSettingData.DoesNotExist:
            setting = await cls.objects.aget(linked_module_class_path=setting)
            data = UserSettingData(for_setting=setting, for_user=user, value=setting.definition.default_value)
        return data

    class Meta:
        indexes = [
            models.Index(fields=['linked_module_class_path'], name='preferences_us_lmcp_vpo', opclasses=["varchar_pattern_ops"]),
        ]


class UserSettingDataQuerySet(RestrictedQuerySet):
    def delete(self, *args, **kwargs):
        for obj in self:
            self.for_setting.definition.on_db_delete(self)
            cache.delete("usersetting-{0}-{1}".format(obj.for_setting.linked_module_class_path, obj.for_user.id))
        return models.QuerySet.delete(*args, **kwargs)


class UserSettingData(models.Model):
    objects = UserSettingDataQuerySet.as_manager()

    value = JSONField()
    for_setting = models.ForeignKey(UserSetting, related_name='data', on_delete=models.CASCADE)
    for_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='settings', on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['for_setting', 'for_user'], name='ut_preferences_usersettingdata_1'),
        ]

    @classmethod
    def view_by_user(cls, setting, user, value):
        if not user.has_perm("preferences.can_usersetting_view_own", setting):
            raise PermissionDenied("You do not have the required permissions")
        if not setting.definition.user_viewable:
            raise PermissionDenied("Not User Viewable")

        try:
            obj = cls.objects.get(for_setting=setting, for_user=user)
        except cls.DoesNotExist:
            value = setting.definition.default_value
        else:
            value = obj.value

        return value

    @classmethod
    def default_by_user(cls, setting, user, value):
        if not user.has_perm("preferences.can_usersetting_edit_own", setting):
            raise PermissionDenied("You do not have the required permissions")
        if not setting.definition.user_editable:
            raise PermissionDenied("Not User Editable")

        try:
            obj = cls.objects.get(for_setting=setting, for_user=user)
            r = obj.delete()
        except cls.DoesNotExist:
            r = None

        return r

    @classmethod
    def update_by_user(cls, setting, user, value):
        try:
            obj = cls.objects.get(for_setting=setting, for_user=user)
        except cls.DoesNotExist:
            obj = cls(for_setting=setting, for_user=user)

        obj.set_by_user(self, value, save=False)
        obj.save()

        return obj

    def set_by_user(self, value, save=True): # maybe do a proxymodel for user edit/access instead
        setting = self.for_setting
        user = self.for_user

        if not user.has_perm("preferences.can_usersetting_edit_own", setting):
            raise PermissionDenied("You do not have the required permissions")
        if not setting.definition.user_editable:
            raise PermissionDenied("Not User Editable")
        try:
            self.value = setting.definition.validate(value, user)
        except ValidationError as e:
            raise PermissionDenied(e or "ValidationError")

        if save:
            return self.save()

    def save(self, *args, **kwargs):
        self.for_setting.definition.on_db_update(self)
        rtn = super().save(*args, **kwargs)
        cache.set("usersetting-{0}-{1}".format(self.for_setting.linked_module_class_path, self.for_user.id), self.value, 60*60*24*2)
        return rtn

    def delete(self, *args, **kwargs):
        self.for_setting.definition.on_db_delete(self)
        cache.delete("usersetting-{0}-{1}".format(self.for_setting.linked_module_class_path, self.for_user.id))
        return super().delete(*args, **kwargs)

    def __str__(self):
        return "Setting {} for User {}".format(self.for_setting_id, self.for_user_id)
