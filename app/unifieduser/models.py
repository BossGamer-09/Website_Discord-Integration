from django.conf import settings
from django.db import models, transaction
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError
from django.utils.functional import cached_property
from django.db.models.functions import Length

from django.contrib.auth.models import AbstractUser
from django.contrib.auth import get_user_model
from django.contrib.auth.models import _user_has_perm
from django.contrib.auth.models import UserManager
from django.contrib import auth
from django.apps import apps

from django_ulid.models import default as default_ulid, ULIDField
from django_otp.models import Device, ThrottlingMixin
from guardian.utils import get_anonymous_user
from guardian.conf.settings import ANONYMOUS_USER_NAME
from asgiref.sync import sync_to_async
from simple_history import register as history_register
from django.contrib.auth.models import Group
from simple_history.models import HistoricalRecords
from ordered_model.models import OrderedModel

from app.preferences.utils import get_user_preference

from .preferences import DisplayNameSource, AvatarSource, CustomDisplayName

from .utils import get_custom_backend
from .validators import CustomUnicodeUsernameValidator

from app.preferences.utils import get_user_preference as _get_user_preference
from app.main.util.db import DBAwareModelMixin, RestrictedQuerySet, OrderedRestrictedQuerySet


history_register(Group, app='app.unifieduser')


def ulid_new():  # fix for duplicating migrations
    return default_ulid()


# TODO: Cog on rank change with signals over redis (wip)


class OrgRank(DBAwareModelMixin, OrderedModel):
    name = models.CharField(max_length=50)
    prefix = models.CharField(max_length=7, blank=True, help_text="Visual prefix for the user (e.g. '[LM]')")
    groups = models.ManyToManyField(Group, blank=True, related_name="ranks", help_text="Django auth groups synced to users with this rank.")

    def save(self, *args, **kwargs):
        # Only trigger group syncing if the rank actually changed
        super().save(*args, **kwargs)

        if self.data_changed(["prefix"]):
            from app.org.signals import pub_rank_update
            for user in self.users:
                cache.delete("uuser-dn-{0}".format(user.pk))
                pub_rank_update(user)

    class Meta(OrderedModel.Meta):
        pass

    def __str__(self):
        return self.name


class OrgPlayerSet(RestrictedQuerySet):
    def delete(self, *args, **kwargs):
        return super().delete(*args, **kwargs)


class OrgPlayerManager(UserManager):
    def get_queryset(self):
        return OrgPlayerSet(self.model, using=self._db)


class OrgPlayer(DBAwareModelMixin, AbstractUser):
    username_validator = CustomUnicodeUsernameValidator()

    id = ULIDField(default=ulid_new, primary_key=True, editable=False)
    rank = models.ForeignKey("OrgRank", on_delete=models.SET_NULL, null=True, blank=True, related_name='users')

    # Member profile extras
    timezone_str   = models.CharField(
        max_length=64, blank=True, default="",
        help_text="IANA timezone string, e.g. 'America/New_York'. Set by the member via /set_timezone.",
    )
    first_event_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of the member's first recorded event attendance. Set automatically.",
    )

    history = HistoricalRecords()

    objects = OrgPlayerManager()

    def __str__(self):
        return self.display_name

    def _user_get_permissions(user, obj, from_name):
        permissions = set()
        name = "get_%s_permissions" % from_name
        if hasattr(user, "oauth") and user.oauth is not None:
            for backend in auth.get_backends():
                if backend.__class__ is get_custom_backend():
                    permissions.update(getattr(backend, name)(user, obj))
        else:
            for backend in auth.get_backends():
                if hasattr(backend, name):
                    permissions.update(getattr(backend, name)(user, obj))
        return permissions

    def _sync_groups_for_rank(self):
        if original_rank_id := self.original_value("rank_id"):
            try:
                old_rank = OrgRank.objects.get(pk=original_rank_id)
                self.groups.remove(*old_rank.groups.all())
            except OrgRank.DoesNotExist:
                pass # The old rank was deleted; groups are likely already cleaned up

        if self.rank_id:
            self.groups.add(*self.rank.groups.all())

    def save(self, *args, **kwargs):
        # Only trigger group syncing if the rank actually changed
        if self.data_changed(["rank_id"]):
            with transaction.atomic():
                self._sync_groups_for_rank()

                super().save(*args, **kwargs)

            from app.org.signals import pub_rank_change

            cache.delete("uuser-dn-{0}".format(self.pk))

            old_rank_id = self.original_value("rank_id", None)
            pub_rank_change(self, old_rank_id, self.rank_id)

        else:
            super().save(*args, **kwargs)

    def clean(self):
        setattr(self, self.USERNAME_FIELD, self.normalize_username(self.username))

    @property
    def is_anonymous(self):
        return ANONYMOUS_USER_NAME == self.username

    def get_setting_value(self, setting_import_path):
        try:
            UserSetting = apps.get_model(app_label='preferences', model_name='UserSetting')
        except Exception:
            return None
        return UserSetting.get_setting_value(setting_import_path, self)

    async def aget_setting_value(self, setting_import_path):
        try:
            UserSetting = apps.get_model(app_label='preferences', model_name='UserSetting')
        except Exception:
            return None
        return await UserSetting.aget_setting_value(setting_import_path, self)

    def get_setting(self, setting_import_path):
        try:
            UserSetting = apps.get_model(app_label='preferences', model_name='UserSetting')
        except Exception:
            return None
        return UserSetting.get_setting_obj(setting_import_path, self)

    async def aget_setting(self, setting_import_path):
        try:
            UserSetting = apps.get_model(app_label='preferences', model_name='UserSetting')
        except Exception:
            return None
        return await UserSetting.aget_setting_obj(setting_import_path, self)

    @property
    def is_authenticated(self):
        return self.is_active

    def get_short_name(self):
        return self.display_name

    def get_pretty_id(self):
        return "[BV:U:{0}]".format(self.id)

    def get_full_name(self):
        return '{0} "{1}"'.format(self.display_name, self.username)

    def get_username(self):
        return self.display_name

    def get_preference(self, import_path):
        return _get_user_preference(import_path, self)

    async def ahas_perm(self, perm, obj=None):
        return await sync_to_async(self.has_perm)(perm, obj)

    def has_perm(self, perm, obj=None):
        if self.is_active and self.is_superuser:
            if not (hasattr(self, "oauth") and self.oauth is not None):
                return True
        if obj:
            return _user_has_perm(self, perm, obj) or _user_has_perm(self, perm, None)
        else:
            return _user_has_perm(self, perm, None)

    def get_user_permissions(self, obj=None):
        return self._user_get_permissions(obj, "user")

    def get_group_permissions(self, obj=None):
        return self._user_get_permissions(obj, "group")

    def get_all_permissions(self, obj=None):
        return self._user_get_permissions(obj, "all")

    @sync_to_async
    def aload_cache(self):
        a = self.display_name
        a = self.avatar_url

    def set_custom_display_name(self, name):
        custom_display_name = self.get_setting(CustomDisplayName.import_path)
        custom_display_name.value = name
        custom_display_name.save()

    async def aset_custom_display_name(self, name):
        custom_display_name = await self.aget_setting(CustomDisplayName.import_path)
        custom_display_name.value = name
        await custom_display_name.asave()

    def set_rank(self, rank=None, rank_pk=None):
        if (rank is None) == (rank_pk is None):
            raise ValueError("Exactly one of 'rank' or 'rank_pk' must be provided.")

        if rank_pk:
            self.rank_id = rank_pk
        else:
            self.rank = rank

        self.save()

    async def aset_rank(self, rank=None, rank_pk=None):
        if (rank is None) == (rank_pk is None):
            raise ValueError("Exactly one of 'rank' or 'rank_pk' must be provided.")

        if rank_pk:
            self.rank_id = rank_pk
        else:
            self.rank = rank

        await self.asave()

    @sync_to_async
    def aget_display_name(self):
        return self.display_name

    @cached_property
    def display_name(self):
        name = cache.get("uuser-dn-{0}".format(self.id))
        if name:
            return name
        else:
            source = get_user_preference(DisplayNameSource.import_path, self)
            if source == 4:  # custom display name with rank
                UserSetting = apps.get_model(app_label='preferences', model_name='UserSetting')
                customdpn = UserSetting.get_setting_value(CustomDisplayName.import_path, self)

                if self.rank_id:
                    return "{} {}".format(self.rank.prefix, customdpn)

                return customdpn
            elif source == 0:  # auto
                try:
                    discorduser = self.discorduser
                except ObjectDoesNotExist:
                    value = self._username_to_display()
                else:
                    value = (discorduser.main_guild_nick or discorduser.global_name
                             or discorduser.full_username or None)
                    if not value or value == "?":
                        value = self._username_to_display()
                    else:
                        return value
            elif source == 1: # discord global
                try:
                    discorduser = self.discorduser
                except ObjectDoesNotExist:
                    value = self._username_to_display()
                else:
                    value = (discorduser.global_name or discorduser.username or None)
                    if not value or value == "?":
                        value = self._username_to_display()
            elif source == 2: # spectrum
                try:
                    rsiuser = self.rsiuser
                except ObjectDoesNotExist:
                    value = self._username_to_display()
                else:
                    value = rsiuser.username
            elif source == 3: # discord main guild
                try:
                    discorduser = self.discorduser
                except ObjectDoesNotExist:
                    value = self._username_to_display()
                else:
                    value = (discorduser.main_guild_nick or discorduser.username or None)
                    if not value or value == "?":
                        value = self._username_to_display()
            else:
                raise ValueError("Setting {} Invalid".format(DisplayNameSource.import_path))
            if not self._state.adding:
                self._update_display_name_cache(value)
                cache.set("uuser-dn-{0}".format(self.id), value, 60*60*24*2)
            return value

    def _username_to_display(self):
        """
        Last-resort display name when DiscordUser is missing or has no name data.
        Checks bot cache using the discord UID embedded in the username pattern,
        then falls back to the raw username (stripped of the discorduid. prefix).
        """
        uid = None
        u = self.username or ""
        if u.startswith("discord_"):
            uid = u[len("discord_"):]
        elif u.startswith("discorduid."):
            uid = u[len("discorduid."):]
        if uid and uid.isdigit():
            bot_cache = cache.get("bot.discorduid.{0}".format(uid)) or {}
            nick = bot_cache.get("membername") or bot_cache.get("username")
            if nick:
                return nick
            # No cache — return just the UID so it's shorter and recognisable
            return "Discord User ({0})".format(uid)
        return u

    def _update_display_name_cache(self, display_name):
        _, created = DisplayNameSearchCache.objects.update_or_create(user=self, defaults={"display_name": display_name})
        return created

    @cached_property
    def avatar_url(self):
        name = cache.get("uuser-au-{0}".format(self.id))
        if name:
            return name
        else:
            source = get_user_preference(AvatarSource.import_path, self)
            if source == 0:  # auto
                try:
                    discorduser = self.discorduser
                except ObjectDoesNotExist:
                    value = False
                else:
                    value = discorduser.avatar_url
            if source == 1:
                try:
                    discorduser = self.discorduser
                except ObjectDoesNotExist:
                    value = False
                else:
                    value = discorduser.avatar_url
            elif source == 2:
                try:
                    rsiuser = self.rsiuser
                except ObjectDoesNotExist:
                    value = False
                else:
                    value = rsiuser.avatar_url
            cache.set("uuser-au-{0}".format(self.id), value, 60*60*24*2)
            return value

    class Meta:
        default_permissions = ()
        permissions = [
            ("view_org_roster", "Can view the org member roster"),
        ]


class DisplayNameSearchCache(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=255, editable=False)

    class Meta:
        default_permissions = ()
        indexes = [
            models.Index(fields=["display_name"], name="unifieduser_display_idx"),
        ]

    @classmethod
    def partial_search(cls, term):
        result = cache.get("DisplayNameSearchCache-partial_search-{0}".format(term))
        if result is None:
            result = []
            for entry in cls.objects.filter(display_name__icontains=term).order_by(Length('display_name').asc()):
                result.append({"user": entry.user_id.str, 'display_name': entry.display_name})
            cache.set("DisplayNameSearchCache-partial_search-{0}".format(term), result, 60)
        return result

    def __str__(self):
        return "Cached {}".format(self.display_name)