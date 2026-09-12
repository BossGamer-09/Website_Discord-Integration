from dynamic_rest.serializers import DynamicModelSerializer
from dynamic_rest.fields import DynamicRelationField, DynamicMethodField, DynamicField
from rest_framework.serializers import BooleanField, Serializer

from . import models


class OrgPlayerSerializer(DynamicModelSerializer):
    class Meta:
        model = models.OrgPlayer
        name = 'orgplayer'
        fields = (
            "id",
            "display_name",
        )


class HasPermSerializer(Serializer):
    has_perm = BooleanField(read_only=True)
