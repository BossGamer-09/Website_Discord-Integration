import functools
import logging

from django.utils.functional import SimpleLazyObject
from django.utils.cache import patch_vary_headers
from django.apps import apps
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect
from django.urls import reverse

from django.contrib.auth import authenticate as django_authenticate
from django_otp.middleware import OTPMiddleware, is_verified
from django_otp import DEVICE_ID_SESSION_KEY

from rest_framework.authentication import SessionAuthentication

logger = logging.getLogger(__name__)

# TODO simplify and combine own oauth on user implementation with lib's


# TODO maybe not needed — see OAuth2BearerAuthentication. If confirmed unused, delete both classes.
class CustomOAuth2TokenMiddleware:
    """
    Middleware for OAuth2 user authentication.
    If it comes after AuthenticationMiddleware and request.user is valid, leave as is.
    If request.user is anonymous, authenticate using the OAuth2 access token.
    Adds "Authorization" to the "Vary" header for correct cache keys.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # CLEAN-02: removed stdout debug prints; use logger.debug if needed.
        if request.META.get("HTTP_AUTHORIZATION", "").startswith("Bearer"):
            if not hasattr(request, "user") or request.user.is_anonymous:
                user = django_authenticate(request=request)
                if user:
                    logger.debug("CustomOAuth2TokenMiddleware: authenticated bearer user")
                    request.user = request._cached_user = user

        response = self.get_response(request)
        patch_vary_headers(response, ("Authorization",))
        return response

    def _oauth_user_field(self, user):
        if not hasattr(user, "oauth"):
            user.oauth = None

        return user


class OAuth2BearerAuthentication(SessionAuthentication):
    def authenticate(self, request):
        # BUG-02: `user` could be referenced unbound when request._user already exists.
        # Initialize from the cached user and only re-authenticate when absent.
        user = getattr(request, "_user", None)
        if user is None:
            user = django_authenticate(request=request)
            if user:
                request.user = user

        request.oauth2_error = getattr(request, "oauth2_error", {})

        # DRF contract: return None (not a tuple) when authentication does not apply.
        if user is None:
            return None

        self.enforce_csrf(request)
        return (user, None)

    def _oauth_user_field(self, user):
        if not hasattr(user, "oauth"):
            user.oauth = None

        return user


class CustomOTPMiddleware(OTPMiddleware):
    """
    Installs after AuthenticationMiddleware. Populates request.user.otp_device and
    user.is_verified(), analogous to AuthenticationMiddleware populating request.user.
    """

    def __call__(self, request):
        try:
            return super().__call__(request)
        except SessionInterrupted:
            # Session deleted mid-request (concurrent logout or kicked user).
            # Log out cleanly and redirect rather than 500-ing.
            auth_logout(request)
            return redirect(reverse("discordauth:login"))

    def _verify_user(self, request, user):
        """Sets OTP-related fields on an authenticated user."""
        user.otp_device = None
        user.is_verified = functools.partial(is_verified, user)

        if user.is_authenticated:
            persistent_id = request.session.get(DEVICE_ID_SESSION_KEY)
            device = self._device_from_persistent_id(persistent_id) if persistent_id else None

            if (device is not None) and (device.user_id != user.pk):
                device = None

            if (device is None) and (DEVICE_ID_SESSION_KEY in request.session):
                del request.session[DEVICE_ID_SESSION_KEY]

            if device is None and hasattr(user, "oauth") and user.oauth is not None:
                # free oauth login (token granted with MFA)
                # BUG-01: previous code dereferenced `device.user_id` while device is None.
                # Validate the candidate (test_device) instead, with guards.
                test_device = getattr(getattr(user.oauth, "access_token_obj", None), "mfa_device", None)
                if test_device is not None and test_device.confirmed and test_device.user_id == user.pk:
                    device = test_device
                    request.session[DEVICE_ID_SESSION_KEY] = device.persistent_id

            elif device is None and getattr(user, "backend", "") == "DiscordAuthBackend":
                # TODO (BUG-03, deferred to migration batch): reconcile how user.backend is set
                # (class vs dotted-string) so this branch is actually reachable.
                DiscordDevice = apps.get_model(app_label="discordauth", model_name="DiscordDevice")

                try:
                    if user.discord.mfa_enabled:
                        test_device, created = DiscordDevice.objects.get_or_create(
                            user=user, defaults={"confirmed": True}
                        )
                        if created:
                            logger.debug("Discord MFA device created for user %s", user.pk)

                        if test_device.confirmed:
                            device = test_device
                            request.session[DEVICE_ID_SESSION_KEY] = device.persistent_id

                except DiscordDevice.DoesNotExist:
                    pass

            user.otp_device = device

        return user
