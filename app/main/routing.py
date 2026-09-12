import traceback
import functools
from importlib import import_module
from urllib.parse import urlparse

from django.apps import apps
from django.conf import settings
from django.utils.http import is_same_domain

from channels.security.websocket import WebsocketDenier, OriginValidator

from rest_framework import exceptions

from channelsmultiplexer import AsyncJsonWebsocketDemultiplexer
from djangochannelsrestframework.consumers import view_as_consumer


def get_demultiplexer(app_label, demultiplexer):
    module = apps.get_app_config(app_label).name
    return getattr(import_module("{}.routing".format(module)), demultiplexer)


def get_consumer(app_label, consumer):
    module = apps.get_app_config(app_label).name
    return getattr(import_module("{}.consumers".format(module)), consumer)


def get_drf_router(app_label):
    module = apps.get_app_config(app_label).name
    return getattr(import_module("{}.urls_api".format(module)), "router")


class SkipCSRFWrapper:
    def __init__(self, func):
        self.func = func
        functools.update_wrapper(self, self.func)

    def __call__(self, request, *args, **kwargs):
        request.csrf_processing_done = True
        return self.func(request, *args, **kwargs)


def get_drf_consumer_views(app_label, plex=None):
    return {
        "{}.{}".format(plex or app_label, name): view_as_consumer(SkipCSRFWrapper(cls.as_view({"put": "create", "get": "retrieve", "head": "list", "patch": "update", "delete": "destroy"})), {"create": "PUT", "update": "PATCH", "list": "HEAD", "retrieve": "GET", "destroy": "DELETE"})
        for name, cls, _ in get_drf_router(app_label).registry
    }


class TokenAuthMiddleware:
    def __init__(self, app):
        # Store the ASGI application we were passed
        self.app = app

    async def validate_token(self, token):
        from rest_framework.authtoken.models import Token

        try:
            token = await Token.objects.select_related("user").aget(key=token)
        except Token.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid token.")

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed("Token User inactive or deleted.")

        if not token.user.is_superuser:
            raise exceptions.AuthenticationFailed("Token User must be superuser.")

        return token

    async def __call__(self, scope, receive, send):
        try:
            for header_name, header_value in scope.get("headers", []):
                if header_name == b"authorization":
                    try:
                        kind, value = header_value.decode("latin1").split()
                    except Exception:
                        kind = None
                    break

            if kind != "Token":
                raise exceptions.AuthenticationFailed("Malformed Token/Auth header.")

            token = await self.validate_token(value)

            scope["user"] = token.user
            scope["token"] = token

        except Exception as exc:
            print("WS Token API Connection denied:")
            print(traceback.format_exception(exc))
            denier = WebsocketDenier()
            return await denier(scope, receive, send)

        print("WS Token API Connection accepted:", token.user.id, token, scope.get("headers", []))
        return await self.app(scope, receive, send)


class AllowedHostsValidator:
    def __init__(self, application, header=b"x-forwarded-host"):
        self.application = application

        allowed_hosts = settings.ALLOWED_HOSTS
        if settings.DEBUG and not allowed_hosts:
            allowed_hosts = ["localhost", "127.0.0.1", "[::1]"]

        self.allowed_hosts = allowed_hosts
        self.check_header = header

    async def __call__(self, scope, receive, send):
        # Extract the Origin header
        test_host = None
        for header_name, header_value in scope.get("headers", []):
            if header_name == self.check_header:
                test_host = header_value.decode("latin1")
                break

        # Check to see if the origin header is valid
        if self.valid_host(test_host):
            # Pass control to the application
            return await self.application(scope, receive, send)
        else:
            # Deny the connection
            denier = WebsocketDenier()
            return await denier(scope, receive, send)

    def valid_host(self, test_host):
        if test_host is None and "*" not in self.allowed_hosts:
            return False
        return self.validate_host(test_host)

    def validate_host(self, test_host):
        return any(
            pattern == "*" or self.match_allowed_host(test_host, pattern)
            for pattern in self.allowed_hosts
        )

    def match_allowed_host(self, test_host, pattern):
        if test_host is None:
            return False

        return is_same_domain(test_host, pattern)
