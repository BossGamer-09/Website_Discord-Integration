from django.db.models import Prefetch
from dynamic_rest.viewsets import DynamicModelViewSet, UPDATE_REQUEST_METHODS, DELETE_REQUEST_METHOD
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.permissions import BasePermission, SAFE_METHODS

from . import serializers
from . import models


class UserSettingViewSetPermission(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return request.user.has_perm("preferences.can_usersetting_view_own", obj)
        return False


class UserSettingViewSet(DynamicModelViewSet):
    http_method_names = ['get', 'options', 'head']
    model = models.UserSetting
    serializer_class = serializers.UserSettingSerializer
    queryset = models.UserSetting.objects.all()
    permission_classes = [UserSettingViewSetPermission]


class UserSettingDataViewSet(viewsets.ViewSet):
    http_method_names = ['get', 'options', 'head']
    serializer_class = serializers.UserSettingDataSerializer
    permission_classes = []

    def list(self, request):
        user = request.user
        r = []

        for setting in models.UserSetting.objects.all():
            if user.has_perm("preferences.can_usersetting_view_own", setting):
                try:
                    data = models.UserSettingData.objects.get(for_setting=setting, for_user=user)
                except models.UserSettingData.DoesNotExist:
                    data = models.UserSettingData(for_setting=setting, for_user=user, value=setting.definition.default_value)
                r.append({"usersetting":{"id":data.for_setting.linked_module_class_path}, "value":data.value})

        return Response({"usersettingdatas":r})

    def retrieve(self, request, pk=None):
        linked_module_class_path = request.data.get("setting", None) or pk

        setting = models.UserSetting.objects.get(linked_module_class_path=linked_module_class_path)
        obj = models.UserSettingData.view_by_user(setting, request.user)

        serializer = serializers.UserSettingDataSerializer(obj)
        return Response(serializer.data)

    def update(self, request, pk=None):
        linked_module_class_path = request.data.get("setting", None) or pk
        value = request.data.get("value")

        setting = models.UserSetting.objects.get(linked_module_class_path=linked_module_class_path)
        obj = models.UserSettingData.update_by_user(setting, user, value)

        serializer = serializers.UserSettingDataSerializer(obj)
        return Response(serializer.data)


"""
class UserSettingOwn(DynamicModelViewSet):
    model = models.UserSetting
    serializer_class = serializers.UserSettingOwn
    queryset = models.UserSetting.objects.all()

    def get_queryset(self):
        user = self.request.user
        r = []

        for item in self.queryset.prefetch_related(Prefetch("data", queryset=models.UserSettingData.objects.filter(for_user=user), to_attr="user_data")):
            if self.is_update() or self.is_delete():
                perm = "preferences.can_usersetting_view_own"
            else:
                perm = "preferences.can_usersetting_edit_own"
            if user.has_perm(perm, item):
                r.append(item)

        return r
"""
