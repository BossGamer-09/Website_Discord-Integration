from importlib import import_module

from django.apps import apps

from channels.security.websocket import WebsocketDenier

from rest_framework import exceptions

from channelsmultiplexer import AsyncJsonWebsocketDemultiplexer
from djangochannelsrestframework.consumers import view_as_consumer

from app.main.routing import get_drf_consumer_views, get_consumer


class TokenAPIDemultiplexer(AsyncJsonWebsocketDemultiplexer):
    """
    Example formats

    {
        "stream": "echo",
        "payload": {"hello": "world"},
    }

    {
        "stream": "users.orgplayer",
        "payload": {
            "request_id":656656,
            "action": "retrieve",
            "parameters":{
                "pk": "01GKXJPC275QA4G3TAN27RW8K8"
            }
        }
    }

    {
        "stream": "users.orgplayer",
        "payload": {
            "action": "list"
        }
    }

    {
        "stream": "subscribe.org.discordrole",
        "payload": {
            "request_id": 1337,
            "action": "subscribe"
        }
    }

    Messages being sent downstream from the Multiplexed consumers will be embedded within a similar style msg.
    """

    applications = (
        {
            "subscribe.org.discordrole": get_consumer("org", "DiscordRoleUpdateConsumer").as_asgi(),
        }
        | get_drf_consumer_views("org", plex="backend")
        | get_drf_consumer_views("preferences")
        | get_drf_consumer_views("unifieduser", plex="users")
    )


class SPAAPIDemultiplexer(AsyncJsonWebsocketDemultiplexer):
    applications = (
        {
#            "sharedmaps.operation": get_consumer("sharedmaps", "OperationConsumer").as_asgi(),
        }
    )