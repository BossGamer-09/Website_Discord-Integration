from django.db import models, transaction, connections, IntegrityError
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils.functional import cached_property
from django.urls import reverse
from django.core.cache import caches
from requests.exceptions import HTTPError
from pprint import pprint
from datetime import datetime, timezone, timedelta

import traceback, requests, logging
from contextlib import suppress
import warnings

logger = logging.getLogger(__name__)

from django_otp.models import Device
from celery_once import AlreadyQueued

from app.preferences.utils import get_global_preference
from app.main.util.db import FastApproxCountQuerySet
from app.main.util.misc import utcnow_aware

from app.org.models import DiscordRole
from app.unifieduser.extra import make_random_password

from app.discordauth.preferences import BotGuildID
from app.discordauth.fields import EncryptedTextField  # SEC-06

from .preferences import DiscordUserGroup
from .extra import get_guild_roles, get_user_info, get_user_guild_info, get_user_guilds, exchange_reply_code, refresh_token
from .errors import RequiresHTTPRequest


class DiscordDevice(Device):
    def verify_token(self, token=None):
        try:
            kind = token["kind"]
            user = token["user"]
        except AttributeError:
            return False
        else:
            # BUG-03: managers are not accessible via model instances; use the class manager.
            exists = DiscordDevice.objects.filter(user=user, confirmed=True).exists()

            if exists and getattr(user, "backend", None) is DiscordAuthBackend and user.discorduser.mfa_enabled and kind == "DiscordDevice":
                return True
            return False

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user'], name="%(app_label)s_%(class)s_user_u")
        ]


class DiscordUserManager(models.Manager):
    def get_queryset(self):
        return FastApproxCountQuerySet(self.model, using=self._db)


class DiscordUser(models.Model):
    discorduid = models.PositiveBigIntegerField(unique=True, primary_key=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name='discorduser', on_delete=models.SET_NULL, null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)  # TODO: split into in discord server(or use separate  model for this?)/oauth
    access_token = EncryptedTextField(null=True, blank=True)  # SEC-06: Fernet-encrypted at rest
    refresh_token = EncryptedTextField(null=True, blank=True)  # SEC-06: Fernet-encrypted at rest
    access_token_expires = models.DateTimeField(null=True, blank=True)

    objects = DiscordUserManager()

    class Meta:
        permissions = [
            ("can_link_discord_accounts", "Can link Discord accounts to site accounts via bot command"),
            ("can_self_link_account", "Can self-link own Discord account via bot command"),
        ]

    def __str__(self):
        return "{0} [{1}]".format(self.full_username, self.discorduid)

    @cached_property
    def user_info(self):
        # PERF-05: read the cached profile lazily on first access instead of on every
        # instantiation/FK access, and never enqueue a Celery refresh from here (that
        # fired per-instantiation and flooded the queue). Refresh happens at login
        # (authenticate -> refresh_cache) and via scheduled tasks; refresh_cache()
        # overwrites this by assigning self.user_info directly.
        if self.access_token and self.pk:
            return caches['default'].get("discorduid.{0}".format(self.discorduid)) or {}
        return {}

    def __repr__(self):
        return repr(self.__str__())

    def save(self, *args, **kwargs):
        # PERF-05: removed the per-save task_refresh_cache enqueue (it fired on every save).
        # Refresh is triggered explicitly at login and via scheduled tasks.
        super().save(*args, **kwargs)

    def task_sync_discord_roles_linked_groups(self, force_refresh_cache=False):
        from .tasks import refresh_discord_roles_linked_groups
        try:
            refresh_discord_roles_linked_groups.delay(self.discorduid, force_refresh_cache)
        except AlreadyQueued:
            logger.debug("refresh_discord_roles_linked_groups already queued for discorduid=%s", self.discorduid)

    def sync_discord_roles_linked_groups(self, raise_if_http_required=False, force_refresh_cache=False):
        if force_refresh_cache and raise_if_http_required:
            raise NotImplementedError("raise_if_http_required = force_refresh_cache = True are not valid together")

        if force_refresh_cache:
            self.refresh_cache(force=True, sync_discord_roles_linked_groups=False)

        try:
            api_roles = [int(role_id) for role_id in self.user_info["user_main_guild_info"]["roles"]]
        except KeyError:
            if force_refresh_cache:
                api_roles = []
            elif raise_if_http_required:
                raise RequiresHTTPRequest()
            else:
                try:
                    self.refresh_cache(force=True, sync_discord_roles_linked_groups=False)
                    api_roles = [int(role_id) for role_id in self.user_info["user_main_guild_info"]["roles"]]
                except Exception as exc:
                    traceback.print_exception(exc)
                    api_roles = []

        obj_filter = DiscordRole.objects.filter(role_id__in=api_roles)

        if obj_filter.count() != len(api_roles):
            if raise_if_http_required:
                raise RequiresHTTPRequest()
            try:
                DiscordRole.update_roles_in_db_from_api()
            except requests.exceptions.HTTPError as exc:
                for role_id in api_roles:
                    DiscordRole.objects.get_or_create(role_id=role_id)
                traceback.print_exception(exc)

        users_old_managed_groups = self.user.groups.filter(discord_roles__isnull=False).distinct()
        users_new_managed_groups = Group.objects.filter(discord_roles__role_id__in=api_roles).distinct()
        # users_new_managed_roles = DiscordRole.objects.filter(permission_groups__isnull=False, role_id__in=api_roles)

        to_remove = set(users_old_managed_groups).difference(set(users_new_managed_groups))  # Elements in users_old_managed_groups but not in users_new_managed_groups
        to_add = set(users_new_managed_groups).difference(set(users_old_managed_groups))  # Elements in users_old_managed_groups but not in users_new_managed_groups

        self.user.groups.remove(*to_remove)
        self.user.groups.add(*to_add)

    def get_full_user_info(self):
        logger.info("get_full_user_info: uid=%s has_access_token=%s", self.discorduid, bool(self.access_token))
        try:
            r = get_user_info(self.access_token)
            logger.info("get_full_user_info: get_user_info returned keys=%s", list(r.keys()) if r else r)
        except HTTPError as e:
            logger.error("get_full_user_info: get_user_info HTTP %s for uid=%s", e.response.status_code, self.discorduid)
            if e.response.status_code == 401:
                self.token_refresh()
            r = get_user_info(self.access_token)

        with suppress(Exception):
            user_guilds = self.get_user_guilds()
            r["user_guilds"] = user_guilds

        try:
            user_main_guild_info = self.get_user_guild_info()
            r["user_main_guild_info"] = user_main_guild_info
        except HTTPError as e:
            # 404 = not in guild (legitimate absence); log everything else but don't raise —
            # a transient Discord API error should not deactivate the user's account
            if e.response.status_code == 404:
                logger.info("get_full_user_info: uid=%s not in main guild (404)", self.discorduid)
            else:
                logger.error("get_full_user_info: guild membership check returned HTTP %s for uid=%s — treating as absent", e.response.status_code, self.discorduid)
        except Exception:
            logger.exception("get_full_user_info: unexpected error fetching guild info for uid=%s — treating as absent", self.discorduid)

        return r

    def get_user_info(self):
        try:
            r = get_user_info(self.access_token)
        except HTTPError as e:
            if e.response.status_code == 401:
                self.token_refresh()
            r = get_user_info(self.access_token)
        return r

    def get_user_guilds(self):
        try:
            r = get_user_guilds(self.access_token)
        except HTTPError as e:
            if e.response.status_code == 401:
                self.token_refresh()
            r = get_user_guilds(self.access_token)
        return r

    def get_user_guild_info(self, guild_id=None):
        if not guild_id:
            guild_id = get_global_preference(BotGuildID.import_path)

        try:
            r = get_user_guild_info(self.access_token, guild_id)
        except HTTPError as e:
            if e.response.status_code == 401:
                self.token_refresh()
            r = get_user_guild_info(self.access_token, guild_id)
        return r

    def token_refresh(self):
        verified_data = refresh_token(reverse("discordauth:loginprocess"), self.refresh_token)
        self.access_token = verified_data["access_token"]
        self.refresh_token = verified_data["refresh_token"]
        self.access_token_expires = utcnow_aware() + timedelta(seconds=verified_data["expires_in"]-10)
        self.save()

    def task_refresh_cache(self, force=False):
        from .tasks import refresh_discorduser_cache_single
        try:
            refresh_discorduser_cache_single.delay(self.discorduid, force=force)
        except AlreadyQueued:
            logger.debug("refresh_discorduser_cache_single already queued for discorduid=%s", self.discorduid)

    def refresh_cache(self, force=True, sync_discord_roles_linked_groups=True):
        logger.info("refresh_cache: uid=%s force=%s has_token=%s", self.discorduid, force, bool(self.access_token))
        if not self.access_token:
            logger.warning("refresh_cache: uid=%s skipped — no access_token", self.discorduid)
            return

        cache_key = "discorduid.{0}".format(self.discorduid)

        if not force:
            time_left = cache.ttl(cache_key) or 0
            if time_left > timedelta(hours=4).total_seconds():
                return

        self.user_info = self.get_full_user_info()
        caches['default'].set(cache_key, self.user_info, timedelta(hours=23).total_seconds())

        if sync_discord_roles_linked_groups:
            self.sync_discord_roles_linked_groups(raise_if_http_required=False)

        logger.debug("discorduser.refresh_cache complete uid=%s", self.discorduid)

    @cached_property
    def bot_cache(self):
        return caches['default'].get("bot.discorduid.{0}".format(self.discorduid)) or {}

    @property
    def username(self):
        return str(self.user_info.get("username") or self.bot_cache.get("username"))

    @property
    def discriminator(self):
        d = self.user_info.get("discriminator") or self.bot_cache.get("discriminator")
        return int(d) if d else None

    @property
    def global_name(self):
        return self.user_info.get("global_name", None) or None

    @property
    def main_guild_nick(self):
        user_main_guild_info = self.user_info.get("user_main_guild_info", None)
        if user_main_guild_info:
            nick = user_main_guild_info.get("nick", None)
            if nick:
                return nick
        # fall back to bot-cached guild display name (set by usermanager on member sync)
        return self.bot_cache.get("membername") or None

    @cached_property
    def main_guild_avatar_url(self):
        guild_id = get_global_preference(BotGuildID.import_path)

        avatar_hash = self.user_info["user_main_guild_info"].get("avatar", None) if "user_main_guild_info" in self.user_info else None

        if not avatar_hash:
            return None

        return "https://cdn.discordapp.com/guilds/{}/users/{}/avatars/{}.{}".format(guild_id, self.discorduid, avatar_hash, "webp?&animated=true" if avatar_hash[:2] == "a_" else "png")

    @cached_property
    def global_avatar_url(self):
        avatar_hash = self.user_info.get("avatar", None)

        if not avatar_hash:
            avatar_hash = self.bot_cache.get("avatar", None)

        if not avatar_hash:
            return None

        return "https://cdn.discordapp.com/avatars/{}/{}.{}".format(self.discorduid, avatar_hash, "gif" if avatar_hash[:2] == "a_" else "png")

    @property
    def mfa_enabled(self):
        return self.user_info.get("mfa_enabled")

    @cached_property
    def avatar_url(self):
        return self.main_guild_avatar_url or self.global_avatar_url

    @cached_property
    def full_username(self):
        return "{0} [{1}]".format((self.main_guild_nick or self.global_name), self.username) if (self.main_guild_nick or self.global_name) else (self.username or "?")

    @classmethod
    def create_with_user(cls, discorduid, access_token, refresh_token, access_token_expires):
        with transaction.atomic():
            try:
                discorduser = cls.objects.get(discorduid=discorduid)
            except cls.DoesNotExist:
                User = get_user_model()
                username = "discorduid.{0}".format(discorduid)
                user = User.objects.filter(
                    username__in=[username, "discord_{0}".format(discorduid)]
                ).order_by("date_joined").first()
                if user is None:
                    user = User.objects.create_user(username=username, password=make_random_password(), is_active=True)
                discorduser = cls.objects.create(discorduid=discorduid, user=user, access_token=access_token, refresh_token=refresh_token, access_token_expires=access_token_expires)
                group_pk = get_global_preference(DiscordUserGroup.import_path)
                if group_pk:
                    group = Group.objects.get(pk=group_pk)
                    user.groups.add(group)
                else:
                    warnings.warn("create_with_user, no Permission Group set for Discord Users")
            else:
                raise ValueError("DiscordUser {0} already exists.".format(discorduser))

        return discorduser

    @classmethod
    def ensure(modelcls, discorduid, access_token, refresh_token, access_token_expires):
        discorduser, created = modelcls.objects.get_or_create(discorduid=discorduid, defaults={"user":None, "access_token":access_token, "refresh_token":refresh_token, "access_token_expires":access_token_expires})
        return (discorduser, created)

    @classmethod
    def _attach_user(modelcls, discorduser, access_token, refresh_token, access_token_expires):
        """Create and attach an OrgPlayer to a DiscordUser that has user=None."""
        from app.discordauth.management.commands.merge_duplicate_discord_users import (
            merge_duplicates_for_uid, _extract_uid,
        )
        uid = discorduser.discorduid
        username = "discorduid.{0}".format(uid)
        User = get_user_model()

        # Auto-merge any pre-existing duplicates before attaching
        try:
            merge_duplicates_for_uid(uid)
        except Exception:
            logger.exception("_attach_user: merge_duplicates_for_uid failed for uid=%s", uid)

        user = User.objects.filter(
            username__in=[username, "discord_{0}".format(uid)]
        ).order_by("date_joined").first()
        if user is None:
            user = User.objects.create_user(username=username, password=make_random_password(), is_active=True)
        discorduser.user = user
        discorduser.access_token = access_token
        discorduser.refresh_token = refresh_token
        discorduser.access_token_expires = access_token_expires
        discorduser.save()
        group_pk = get_global_preference(DiscordUserGroup.import_path)
        if group_pk:
            with suppress(Exception):
                group = Group.objects.get(pk=group_pk)
                user.groups.add(group)
        else:
            warnings.warn("_attach_user: no Permission Group set for Discord Users")
        return user

    @classmethod
    def ensure_with_user(modelcls, discorduid, access_token, refresh_token, access_token_expires):
        # Fast path: DiscordUser exists and already has an OrgPlayer linked
        try:
            discorduser = modelcls.objects.select_related("user").exclude(user=None).get(discorduid=discorduid)
            if access_token is not None:
                discorduser.access_token = access_token
                discorduser.refresh_token = refresh_token
                discorduser.access_token_expires = access_token_expires
                modelcls.objects.filter(pk=discorduser.pk).update(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    access_token_expires=access_token_expires,
                )
            return (discorduser, False)
        except modelcls.DoesNotExist:
            pass

        # Slow path: need to create or repair
        with transaction.atomic():
            discorduser, du_created = modelcls.objects.get_or_create(
                discorduid=discorduid,
                defaults={
                    "user": None,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "access_token_expires": access_token_expires,
                },
            )

            # Lock the row so concurrent calls don't both enter _attach_user
            discorduser = modelcls.objects.select_for_update().get(pk=discorduser.pk)

            if discorduser.user_id is None:
                modelcls._attach_user(discorduser, access_token, refresh_token, access_token_expires)
                created = True
            else:
                created = False

        return (discorduser, created)


class DiscordAuthBackend(object):
    def authenticate(self, request, discord_resp_param=None):
        logger.debug("DiscordAuthBackend.authenticate attempt")
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            return None
        try:
            verified_data = exchange_reply_code(reverse("discordauth:loginprocess"), discord_resp_param["code"])
            user_info = get_user_info(verified_data["access_token"])

            verified_data.update(user_info)
            verified_data["access_token_expires"] = utcnow_aware() + timedelta(seconds=verified_data["expires_in"]-10)

        except Exception as outer_exc:
            logger.exception("DiscordAuthBackend.authenticate: token exchange/user_info failed")
            raise outer_exc
        else:
            if not verified_data:
                return None

        discorduser, created = DiscordUser.ensure_with_user(discorduid=verified_data["id"], access_token=verified_data["access_token"], refresh_token=verified_data["refresh_token"], access_token_expires=verified_data["access_token_expires"])
        authed_user = discorduser.user

        logger.info("authenticate: uid=%s created=%s user_pk=%s is_active=%s", discorduser.discorduid, created, authed_user.pk, authed_user.is_active)

        authed_user.backend = self.__class__

        # Always refresh cache on login so guild membership is current
        try:
            discorduser.refresh_cache(force=True)
            logger.info("authenticate: refresh_cache done uid=%s user_info_keys=%s guild_info=%s",
                discorduser.discorduid,
                list(discorduser.user_info.keys()) if discorduser.user_info else None,
                bool(discorduser.user_info.get("user_main_guild_info")) if discorduser.user_info else None,
            )
        except Exception:
            logger.exception("authenticate: refresh_cache failed for uid=%s", discorduser.discorduid)

        if not self.check_requirements(discorduser):
            logger.warning("authenticate: check_requirements FAILED uid=%s user_info=%s", discorduser.discorduid, discorduser.user_info)
            authed_user.is_active = False
            authed_user.save()
            return None

        if not authed_user.is_active:
            # Re-activate if they previously failed but now pass (rejoined guild)
            authed_user.is_active = True
            authed_user.save()

        logger.info("DiscordAuthBackend.authenticate success uid=%s", discorduser.discorduid)

        return authed_user

    @classmethod
    def add_as_secondary(cls, request, discord_resp_param):
        logger.debug("DiscordAuthBackend.add_as_secondary attempt")
        if not request.user.is_authenticated:
            raise PermissionDenied("User is not authenticated")
        verified_data = exchange_reply_code(reverse("discordauth:addprocess"), discord_resp_param["code"])
        user_info = get_user_info(verified_data["access_token"])

        discorduid = user_info["id"]

        try:
            discorduser = DiscordUser.objects.get(discorduid=discorduid)
        except DiscordUser.DoesNotExist:
            discorduser = DiscordUser(discorduid=discorduid)
        else:
            if discorduser.user:
                raise PermissionDenied("Discord UID is already linked to another account")

        discorduser.access_token = verified_data["access_token"]
        discorduser.refresh_token = verified_data["refresh_token"]
        discorduser.access_token_expires = verified_data["access_token_expires"]
        discorduser.user = request.user

        discorduser.save()

        if not discorduser.user_info:  # TODO: probs make this the task for release
            discorduser.refresh_cache(force=True)

        return discorduser

    @staticmethod
    def check_requirements(discorduser):
        """Return True only if the user is a member of the main guild."""
        guild_info = discorduser.user_info.get("user_main_guild_info") if discorduser.user_info else None
        return bool(guild_info)

    def get_user(self, user_id=None, discorduid=None):
        if user_id != None:
            try:
                discorduser = DiscordUser.objects.get(user__pk=user_id)
                return discorduser.user
            except Exception:
                return None
        elif discorduid:
            try:
                discorduser = DiscordUser.objects.get(discorduid=discorduid)
                return discorduser.user
            except Exception:
                return None
        else:
            raise ValueError("user_id and discorduid cannot both be None")
