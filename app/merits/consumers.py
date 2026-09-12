import json
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels import DEFAULT_CHANNEL_LAYER
from channels.db import database_sync_to_async

log = logging.getLogger(__name__)


def _room(pk):
    return f"merits_chat_{pk}"


class MeritChatConsumer(AsyncJsonWebsocketConsumer):
    channel_layer_alias = DEFAULT_CHANNEL_LAYER
    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            log.warning("MeritChat: unauthenticated connect rejected")
            await self.close()
            return

        self.pk = self.scope["url_route"]["kwargs"]["pk"]

        allowed = await self._can_chat(user, self.pk)
        if not allowed:
            log.warning("MeritChat: access denied pk=%s user=%s", self.pk, user)
            await self.close()
            return

        self.room    = _room(self.pk)
        self.display = str(getattr(user, "display_name", None) or user.username)
        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.accept()
        log.info("MeritChat: connected pk=%s user=%s", self.pk, user)

    async def disconnect(self, code):
        if hasattr(self, "room"):
            await self.channel_layer.group_discard(self.room, self.channel_name)

    async def receive_json(self, content):
        text = (content.get("message") or "").strip()[:500]
        if not text:
            return

        # Check request is still open before relaying
        from .models import MeritRequest
        req = await MeritRequest.objects.filter(pk=self.pk).afirst()
        if not req or req.status in (MeritRequest.Status.FULFILLED, MeritRequest.Status.DENIED):
            await self.send_json({"type": "closed"})
            await self.close()
            return

        await self.channel_layer.group_send(self.room, {
            "type":    "chat.message",
            "sender":  self.display,
            "message": text,
        })

    async def chat_message(self, event):
        await self.send_json({
            "type":    "message",
            "sender":  event["sender"],
            "message": event["message"],
        })

    @database_sync_to_async
    def _can_chat(self, user, pk):
        from .models import MeritRequest
        try:
            req = MeritRequest.objects.get(pk=pk)
        except MeritRequest.DoesNotExist:
            return False
        if req.status in (MeritRequest.Status.FULFILLED, MeritRequest.Status.DENIED):
            return False
        # Requester or anyone with view/review/fulfill perm
        if req.requester_id == user.pk:
            return True
        return (
            user.has_perm("merits.view_merit_requests") or
            user.has_perm("merits.review_merit_request") or
            user.has_perm("merits.fulfill_merit_request")
        )
