from django.contrib.auth import get_user_model

from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.decorators import action

from app.unifieduser.serializers import OrgPlayerSerializer

from .serializers import DiscordRoleSerializer
from . import models


class DiscordRoleUpdateConsumer(GenericAsyncAPIConsumer):
    queryset = get_user_model().objects.all()
    serializer_class = OrgPlayerSerializer

    @model_observer(models.DiscordRole)
    async def discord_role_activity(self, message: DiscordRoleSerializer, observer=None, subscribing_request_ids=[], **kwargs):
        await self.send_json(dict(message.data))

    @discord_role_activity.serializer
    def discord_role_activity(self, instance: models.DiscordRole, action, **kwargs) -> DiscordRoleSerializer:
        """This will return the related serializer"""
        return DiscordRoleSerializer(instance)

    @action()
    async def subscribe(self, request_id, **kwargs):
        await self.discord_role_activity.subscribe(request_id=request_id)
