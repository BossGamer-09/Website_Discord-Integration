from dynamic_rest.serializers import DynamicModelSerializer, DynamicEphemeralSerializer
from dynamic_rest.fields import DynamicRelationField, DynamicMethodField, DynamicField, DynamicComputedField

from rest_framework import serializers

from . import models


class UserSettingDataSerializer(DynamicModelSerializer): #(DynamicEphemeralSerializer):
    class Meta:
        model = models.UserSettingData
        name = 'usersettingdata'
        fields = (
            "value",
            "for_user",
            "for_setting",
        )

    for_user = DynamicRelationField('app.unifieduser.serializers.OrgPlayerSerializer')
    for_setting = DynamicRelationField('UserSettingSerializer', many=True)


class UserSettingSerializer(DynamicModelSerializer):
    class Meta:
        model = models.UserSetting
        name = 'usersetting'
        fields = (
            "name",
            "description",
            "id",
            "default_value",
            "data",
            "widget_class",
            "client_schema",
            "choices",
        )

    def get_default_value(self, obj):
        return getattr(obj.definition, "default_value", None)

    def get_widget_class(self, obj):
        return getattr(obj.definition, "widget_class", None).__name__

    def get_client_schema(self, obj):
        return getattr(obj.definition, "client_schema", None)

    def get_choices(self, obj):
        return getattr(obj.definition, "choices", None)

    id = DynamicField(source="linked_module_class_path")
    data = DynamicRelationField('UserSettingDataSerializer', many=True, deferred=True)
    default_value = DynamicMethodField(read_only=True)
    widget_class = DynamicMethodField(read_only=True)
    client_schema = DynamicMethodField(read_only=True)
    choices = DynamicMethodField(read_only=True)
