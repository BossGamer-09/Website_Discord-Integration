import os
import django
import asyncio

from urllib.parse import urlparse

from django.core.asgi import get_asgi_application
from django.urls import re_path
from django.http.request import is_same_domain

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except Exception:
    import warnings
    warnings.warn("Couldn't set uvloop as event_loop_policy, using slower default.")
else:
    print("Using uvloop")

django_asgi_app = get_asgi_application()

from .routing import get_demultiplexer, TokenAuthMiddleware, AllowedHostsValidator

application = ProtocolTypeRouter({
    "http": django_asgi_app,
})
