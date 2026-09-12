from django.db.models import Prefetch
from django.contrib.auth.models import Group

from rest_framework.permissions import IsAdminUser  # SEC-08
from dynamic_rest.viewsets import DynamicModelViewSet

from . import serializers
from . import models


# SEC-08: these viewsets expose Django auth Groups and Discord roles. They are currently
# reached only over the superuser-gated WebSocket demultiplexer, but with no
# permission_classes they rely entirely on transport auth — anyone HTTP-mounting this
# router would get AllowAny CRUD on groups/roles. Pin them explicitly so they fail closed.
class GroupViewSet(DynamicModelViewSet):
    model = Group
    serializer_class = serializers.GroupSerializer
    queryset = models.Group.objects.all()
    permission_classes = [IsAdminUser]


class DiscordRoleViewSet(DynamicModelViewSet):
    model = models.DiscordRole
    serializer_class = serializers.DiscordRoleSerializer
    queryset = models.DiscordRole.objects.all()
    permission_classes = [IsAdminUser]
