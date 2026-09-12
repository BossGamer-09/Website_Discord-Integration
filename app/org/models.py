from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import models
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.utils.functional import cached_property
from django.conf import settings
from django.db.models.expressions import F
from django.db.models import Q
from django.db.models import Value

from django_redis import get_redis_connection
from django.core.cache import caches

from django.db.models.functions import Length
from django.contrib.auth.models import Group

from django import forms

from autoslug import AutoSlugField
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import models, transaction
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import JSONField
from django.core.exceptions import ValidationError
from django.core.exceptions import *
from django.db import models
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from simple_history.models import HistoricalRecords

from ordered_model.models import OrderedModel, OrderedModelManager
from simple_history.models import HistoricalRecords
from celery_once import AlreadyQueued

from app.discordauth.preferences import BotGuildID, BotToken
from app.discordauth.extra import get_guild_roles
from app.preferences.utils import get_global_preference
from app.main.util.db import DBAwareModelMixin, RestrictedQuerySet, OrderedRestrictedQuerySet

from app.discordauth.errors import RequiresHTTPRequest
from app.discordauth.tasks import refresh_discord_roles_linked_groups

from .signals import pub_user_add, pub_user_remove


class DiscordRole(DBAwareModelMixin, models.Model):
    role_id = models.BigIntegerField(primary_key=True)
    discord_order = models.IntegerField(null=True, blank=True)
    permission_groups = models.ManyToManyField(Group, related_name="discord_roles", through='GroupDiscordRole')
    # temp_for_event = models.OneToOneField("events.Event", null=True, blank=True, related_name="temp_discord_role", on_delete=models.CASCADE)

    @classmethod
    def update_roles_in_db_from_api(cls):
        bot_token = get_global_preference(BotToken.import_path)
        guild_id = get_global_preference(BotGuildID.import_path)

        roles = get_guild_roles(bot_token, guild_id)
        roles = [role for role in roles if role["name"] != "@everyone" and not role.get("managed", None)]

        to_remove = cls.objects.exclude(role_id__in=[int(role["id"]) for role in roles])
        to_remove.delete()

        for role in roles:
            obj, created = cls.objects.update_or_create(role_id=int(role["id"]), create_defaults={"discord_order": int(role["position"])})

            cache_dict = {"name": role.get("name", None), "emoji-url": role.get("icon", None) or None, "emoji-code": role.get("unicode_emoji", None) or None, "color": role.get("color", None) or None}
            cache.set("discordrole.org.{0}".format(obj.role_id), cache_dict, None)

    @cached_property
    def cached_data(self):
        empty = {"name": None, "emoji-url": None, "emoji-code": None, "color": None}
        return cache.get("discordrole.org.{0}".format(self.role_id)) or empty

    @property
    def name(self):
        return self.cached_data["name"]

    @property
    def emoji_url(self):
        return self.cached_data["emoji-url"]

    @property
    def emoji_code(self):
        return self.cached_data["emoji-code"]

    @property
    def color(self):
        return self.cached_data["color"]

    @property
    def color_rgb(self):
        if not self.color:
            return None

        color = int(self.color)

        r = (color >> 16) & 0xFF
        g = (color >> 8) & 0xFF
        b = color & 0xFF

        return r, g, b

    def __str__(self):
        return "{} [{}]".format(self.name, self.role_id)

    def _on_users_add(self, users):
        for obj in users:
            pub_user_add(obj)

    def _on_users_remove(self, users):
        for obj in users:
            pub_user_remove(obj)

    def validate_constraints(self, exclude=None):
        if not self._state.adding:  # change Object
            if self.data_changed(["role_id"]):
                raise ValidationError([{NON_FIELD_ERRORS: ['Cannot update role_id on instance of "DiscordRole", recreate instead.']}])
        else:
            pass
            # TODO probs should verify this role exists on discord, or maybe not
        return super().validate_constraints(exclude=exclude)

    def save(self, *args, **kwargs):
        self.validate_constraints()
        super().save(*args, **kwargs)

    class Meta:
        default_permissions = ()
        ordering = ('-discord_order',)
        permissions = [
            ("can_manage_discord_members", "Can manage Discord members via bot commands"),
        ]


class GroupDiscordSet(RestrictedQuerySet):
    def bulk_create(self, *args, **kwargs):
        objs = models.QuerySet.bulk_create(*args, **kwargs)

        discordusers = dict()

        for obj in objs:
            users = obj.group.user_set.all()
            obj.discord_role._on_users_add(users)

            for user in users:
                discordusers[user.discorduser.discorduid] = user.discorduser

        for discorduser in discordusers.values():
            try:
                discorduser.sync_discord_roles_linked_groups(raise_if_http_required=True)
            except RequiresHTTPRequest:
                discorduser.task_sync_discord_roles_linked_groups()

        return objs

    def delete(self, *args, **kwargs):
        for obj in self:
            users = obj.group.user_set.all()
            obj.discord_role._on_users_remove(users)
        return models.QuerySet.delete(*args, **kwargs)


class GroupDiscordRole(DBAwareModelMixin, models.Model):
    discord_role = models.ForeignKey(DiscordRole, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)

    objects = GroupDiscordSet.as_manager()

    def validate_constraints(self, exclude=None):
        if not self._state.adding:
            raise ValidationError([{NON_FIELD_ERRORS: ['Cannot update an instance of "GroupDiscordRole", recreate instead.']}])
        return super().validate_constraints(exclude=exclude)

    @transaction.atomic()
    def save(self, *args, **kwargs):
        self.validate_constraints()

        users = self.group.user_set.all()
        self.discord_role._on_users_add(users)

        super().save(*args, **kwargs)

        for user in users:
            user.discorduser.task_sync_discord_roles_linked_groups()

    @transaction.atomic()
    def delete(self, *args, **kwargs):
        users = self.group.user_set.all()
        self.discord_role._on_users_remove(users)

        super().delete(*args, **kwargs)

        for user in users:
            user.discorduser.task_sync_discord_roles_linked_groups()

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=['discord_role', 'group'], name='ut_org_groupdiscordrole_1'),
        ]


class DiscordUserGuildEvent(models.Model):
    class Meta:
        default_permissions = ()

    class Kind(models.TextChoices):
        USER_JOIN = 'user_join', 'User Join'
        USER_LEAVE = 'user_leave', 'User Leave'
        USER_DELETE = 'user_delete', 'User Update'
        USER_UPDATE = 'user_update', 'User Update'
        USER_NEWDETECT = 'user_new', 'User Newly Detected'  # probably joined when bot down or before bot ran 1st

    discorduser = models.ForeignKey("discordauth.DiscordUser", related_name='guildevents', on_delete=models.CASCADE)
    kind = models.CharField(max_length=40, choices=Kind.choices, db_index=True)
    datetime = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(null=True, blank=True)


class OrgPlayerNote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notes')
    message = models.TextField(help_text="The content of the note about the user.")
    date = models.DateTimeField(auto_now_add=True)
    
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.CharField(max_length=255, null=True, blank=True, help_text="The PK of the related object.")

    related_object = GenericForeignKey('content_type', 'object_id')

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ['-date']
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"Note for {self.user.display_name} on {self.date.strftime('%Y-%m-%d')}"


# UserJoinRecord moved to app.disfunction.models


# ---------------------------------------------------------------------------
# Activity ping roles — self-assignable opt-in roles (PvP, Mining, etc.)
# ---------------------------------------------------------------------------

class ActivityPingRole(DBAwareModelMixin, models.Model):
    """
    A self-assignable Discord role members can opt into to receive pings for
    specific activities (PvP, Mining, Salvage, etc.).
    """

    CATEGORY_DISCIPLINE = "Discipline"
    CATEGORY_OTHER      = "Other Roles"

    discord_role   = models.OneToOneField(
        DiscordRole,
        on_delete=models.CASCADE,
        related_name='activity_ping_role',
    )
    label          = models.CharField(max_length=80, help_text="Display name in the portal button/embed.")
    description    = models.CharField(max_length=200, blank=True, default="")
    emoji          = models.CharField(max_length=32, blank=True, default="", help_text="Emoji shown next to label (unicode or :name:).")
    category       = models.CharField(
        max_length=80, blank=True, default=CATEGORY_DISCIPLINE,
        help_text='Section heading in the portal embed (e.g. "Discipline" or "Other Roles").',
        db_index=True,
    )
    display_order  = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active      = models.BooleanField(default=True, help_text="Inactive roles are hidden from the portal.")
    min_rank       = models.ForeignKey(
        'unifieduser.OrgRank',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
        help_text="Minimum OrgRank required to self-assign. Leave blank for no restriction.",
    )

    class Meta:
        default_permissions = ()
        ordering = ['category', 'display_order', 'label']
        permissions = [
            ('manage_activity_ping_roles', 'Can manage activity ping roles'),
        ]

    def __str__(self):
        return self.label


class PrivateNotifyThread(models.Model):
    """Maps a Discord user ID to their persistent private notification thread."""
    user_id   = models.BigIntegerField(unique=True, primary_key=True)
    thread_id = models.BigIntegerField(unique=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"PrivateNotify: User {self.user_id} → Thread {self.thread_id}"


class AnnounceAuditLog(models.Model):
    """Persistent record of every announcement approval/denial decision."""

    class Decision(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        DENIED   = "DENIED",   "Denied"
        SENT     = "SENT",     "Sent Direct"  # approval bypassed

    decision        = models.CharField(max_length=10, choices=Decision.choices, db_index=True)
    submitter_id    = models.BigIntegerField(help_text="Discord user ID of the submitter")
    reviewer_id     = models.BigIntegerField(null=True, blank=True, help_text="Discord user ID of the approver/denier")
    audience        = models.CharField(max_length=120, blank=True)
    body_preview    = models.TextField(blank=True, help_text="First 500 chars of announcement body")
    denial_reason   = models.TextField(blank=True)
    target_channel_id = models.BigIntegerField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at"]
        verbose_name = "Announce Audit Log"
        verbose_name_plural = "Announce Audit Logs"

    def __str__(self):
        return f"{self.decision} by {self.reviewer_id} at {self.created_at:%Y-%m-%d %H:%M}"


class ActivityPingPortal(models.Model):
    """
    Tracks the Discord message containing the ping-role selection UI so it
    can be updated when roles change.
    """
    channel_id = models.BigIntegerField(unique=True)
    message_id = models.BigIntegerField(unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"Portal in channel {self.channel_id}"
