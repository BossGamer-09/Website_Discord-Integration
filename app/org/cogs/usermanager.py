import asyncio
from datetime import datetime
from inspect import iscoroutinefunction
from django.contrib.auth import get_user_model
from django.core.exceptions import SynchronousOnlyOperation
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async, async_to_sync
from datetime import timedelta
from django.conf import settings
from django.db.models import aprefetch_related_objects
import json
from .. import signals
from app.preferences.signals import PREFERENCES_PUBSUB_CHANNEL

import logging
import discord
from discord.ext import commands
from discord.ext import tasks
from discord import app_commands
from contextlib import suppress
import redis.asyncio as redis
from django.apps import apps
from django.db.models import Q

from guardian.utils import get_anonymous_user

from app.discordauth.models import DiscordUser
from app.main.util.discord_command_checks import requires_django_perm

from django_redis import get_redis_connection
from django.core.cache import cache, caches

from app.main.util.misc import utcnow_aware

from ..models import DiscordRole, DiscordUserGuildEvent
from app.unifieduser.preferences import DisplayNameSource, CustomDisplayName



# Helper to serialize Discord user/member objects to JSON‑compatible dict
def _serialize_discord_user(user):
    """Convert a discord.User or discord.Member into a JSON‑serializable dict."""
    data = {
        "id": user.id,
        "name": user.name,
        "display_name": getattr(user, "display_name", user.name),
        "discriminator": getattr(user, "discriminator", None),
        "avatar_url": str(user.avatar.url) if user.avatar else None,
        "bot": user.bot,
    }
    # system attribute only exists on discord.Member
    if hasattr(user, "system"):
        data["system"] = user.system
    return data


def gvars(obj):
    return {attr: getattr(obj, attr) for attr in dir(obj) if not attr.startswith('_') and not iscoroutinefunction(getattr(obj, attr)) and not callable(getattr(obj, attr))}


class UserManagerCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.redis_client = None
        self.pubsub = None
        self.task_key_sync_user_with_backend = "UserManagerCog.sync_user_with_backend"
        self.task_key_sync_roles_to_backend = "UserManagerCog.sync_roles_to_backend"
        self.tasks = {}
        self.OrgRank = apps.get_model(app_label='unifieduser', model_name='OrgRank')
        self.DiscordUser = apps.get_model(app_label='discordauth', model_name='DiscordUser')
        self.user_model = get_user_model()

        self.startup_task.start()

    async def cog_unload(self):
        for key, task in self.tasks.items():
            if not task.done():
                task.cancel()

        self.startup_task.cancel()
        self.sync_listener_loop.cancel()
        try:
            await self.redis_client.aclose()
        except Exception:
            pass

    @tasks.loop(count=1)
    async def startup_task(self):
        redis_url = settings.CACHES['default']['LOCATION']
        pool = redis.ConnectionPool.from_url(redis_url)
        self.redis_client = redis.Redis.from_pool(pool)

        self.pubsub = self.redis_client.pubsub()
        self.sync_listener_loop.start()

        self.logger.info("Starting full UserManagerCog sync...")
        await self.full_sync()

    @startup_task.before_loop
    async def before_startup_task(self):
        # Pause till set up
        await self.client.wait_until_ready()

    # Redis Listener:
    @tasks.loop(seconds=0.1)
    async def sync_listener_loop(self):
            if not self.pubsub.subscribed:
                # The imports are removed from here
                await self.pubsub.subscribe(signals.PUBSUB_CHANNEL, PREFERENCES_PUBSUB_CHANNEL)

            try:
                message = await self.pubsub.get_message(ignore_subscribe_messages=True)
                if message:
                    raw_channel = message['channel']
                    channel = raw_channel.decode('utf-8') if isinstance(raw_channel, bytes) else raw_channel
                    data = json.loads(message['data'])
                    # This will now work correctly regardless of subscription state
                    if channel == PREFERENCES_PUBSUB_CHANNEL:
                        await self.process_event_preference(data)
                    elif channel == signals.PUBSUB_CHANNEL:
                        await self.process_event(data)
                    else:
                        self.logger.warning("Received message on unexpected Pub/Sub channel: %s", channel)
            except Exception:
                self.logger.exception("User Sync Error:")

    async def process_event_preference(self, data):
        action = data.get('action')

        if action in ["UserSettingData.delete", "UserSettingData.update"]:
            path = data.get('path')
            if path == CustomDisplayName.import_path:
                user_id = data.get('user_id')
                await self.on_backend_signal_user_name_change(user_id)

    async def process_event(self, data):
        action = data.get('action')
        obj_type = data.get('type')

        if obj_type == "unifieduser.User":
            if action == "User.OrgRank.rename":
                obj_id = data.get('id')
                await self.on_backend_signal_user_name_change(obj_id)
                return

        elif obj_type == "unifieduser.OrgRank":
            if action == "User.update":
                obj_id = data.get('id')
                await self.on_backend_signal_user_name_change(obj_id)

                rank_before_id = data.get('rank_before_id')
                rank_after_id = data.get('rank_after_id')
                await self.on_backend_signal_rank_change(obj_id, rank_before_id, rank_after_id)

                return

        elif obj_type == "org.DiscordRole":
            if action in ["User.add", "User.remove"]:
                obj_id = data.get('id')
                await self.on_backend_signal_user_change(obj_id)
                return

        self.logger.debug("Unhandled Pub/Sub event — type=%s action=%s", obj_type, action)

    async def sync_user_with_backend(self):
        db_member_pks = set([x async for x in DiscordUser.objects.all().values_list("pk", flat=True)])

        raw_guild_id = await self.client.aget_main_guild_id()
        guild = self.client.get_guild(raw_guild_id)

        async for member in guild.fetch_members(limit=None):
            if member.id in db_member_pks:
                # Always seed bot cache regardless of whether OrgPlayer lookup succeeds
                cache_key = "bot.discorduid.{0}".format(member.id)
                await sync_to_async(cache.set)(cache_key, {
                    "username": member.name,
                    "membername": member.display_name,
                    "discriminator": member.discriminator,
                    "avatar": member.display_avatar.url if member.display_avatar else None,
                }, None)

                try:
                    user = await self.user_model.objects.select_related(
                        "discorduser", "rank", "displaynamesearchcache"
                    ).aget(discorduser__pk=member.id)
                except Exception:
                    continue

                await self.sync_display_name(user, member)
                await self.sync_managed_user_roles(user, member)
                continue

            discorduser, _ = await self.add_or_update_member(member)
            user = discorduser.user or await database_sync_to_async(get_anonymous_user)()
            await self.sync_managed_user_roles(user, member)

        self.logger.info('Retrieved all Guild Members {}'.format(utcnow_aware()))
        del self.tasks[self.task_key_sync_user_with_backend]

    async def sync_roles_to_backend(self):
        present_roles = []

        raw_guild_id = await self.client.aget_main_guild_id()
        guild = self.client.get_guild(raw_guild_id)

        for role in guild.roles:
            if role.is_assignable():
                present_roles.append(role.id)
                discord_role, _ = await DiscordRole.objects.aupdate_or_create(role_id=role.id, defaults={'discord_order': role.position})
                await sync_to_async(cache.set)("discordrole.org.{0}".format(discord_role.role_id), {"name": role.name, "emoji-url": role.icon.url if role.icon else None, "emoji-code": role.unicode_emoji or None, "color": role.color.value}, None)  # cache forever

        await DiscordRole.objects.exclude(pk__in=present_roles).adelete()

        self.logger.info('Synced all Guild Roles to backend {}'.format(utcnow_aware()))
        del self.tasks[self.task_key_sync_roles_to_backend]

    async def full_sync(self):
        task = asyncio.create_task(self.sync_user_with_backend(), name=self.task_key_sync_user_with_backend)
        self.tasks[self.task_key_sync_user_with_backend] = task

        task = asyncio.create_task(self.sync_roles_to_backend(), name=self.task_key_sync_roles_to_backend)
        self.tasks[self.task_key_sync_roles_to_backend] = task

        self.logger.info("Full sync started.")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        raw_guild_id = await self.client.aget_main_guild_id()
        guild = self.client.get_guild(raw_guild_id)

        if role.guild != guild:
            return
        if not role.is_assignable():
            return

        discord_role, _ = await DiscordRole.objects.acreate(role_id=role.id, discord_order=role.position)
        await sync_to_async(cache.set)("discordrole.org.{0}".format(discord_role.role_id), {"name": role.name, "emoji-url": role.icon.url if role.icon else None, "emoji-code": role.unicode_emoji or None, "color": role.color.value}, None)  # cache forever

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        raw_guild_id = await self.client.aget_main_guild_id()
        guild = self.client.get_guild(raw_guild_id)

        if role.guild != guild:
            return
        if not role.is_assignable():
            return

        await DiscordRole.objects.filter(pk=role.id).adelete()

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        role = after

        raw_guild_id = await self.client.aget_main_guild_id()
        guild = self.client.get_guild(raw_guild_id)

        if role.guild != guild:
            return
        if not role.is_assignable():
            await DiscordRole.objects.filter(pk=role.id).adelete()
            return

        discord_role, _ = await DiscordRole.objects.aupdate_or_create(role_id=role.id, defaults={'discord_order': role.position})
        await sync_to_async(cache.set)("discordrole.org.{0}".format(discord_role.role_id), {"name": role.name, "emoji-url": role.icon.url if role.icon else None, "emoji-code": role.unicode_emoji or None, "color": role.color.value}, None) 

    @commands.Cog.listener()
    async def on_user_update(self, before, after):
        if after.bot:
            return

        # Only track updates for users who have already authenticated via the portal.
        # ensure_with_user would create an OrgPlayer with no access token, which breaks login.
        try:
            discorduser = await DiscordUser.objects.select_related("user").aget(discorduid=after.id)
        except DiscordUser.DoesNotExist:
            return

        created = False
        await DiscordUserGuildEvent.objects.acreate(
            kind=DiscordUserGuildEvent.Kind.USER_UPDATE,
            details={
                "new_discord_user": created,
                "user-before": _serialize_discord_user(before),
                "user-after": _serialize_discord_user(after)
            },
            discorduser=discorduser
        )

    @commands.Cog.listener()
    async def on_member_update(self, before, after):  # this will get called when the bot changes a users nickname, i guess that's ok
        # role check for new role added
        if len(before.roles) < len(after.roles):
            new_role = next(role for role in after.roles if role not in before.roles)
            managed_role = await DiscordRole.objects.filter(permission_groups__isnull=False, role_id=new_role.id).aexists()
            if managed_role:
                User = await sync_to_async(get_user_model)()
                should_have_role = await User.objects.filter(groups__discord_roles__role_id=new_role.id, discorduser__discorduid=after.id).aexists()

                if not should_have_role:
                    await after.remove_roles(new_role)

        # role check for role removed
        elif len(before.roles) > len(after.roles):
            old_role = next(role for role in before.roles if role not in after.roles)
            managed_role = await DiscordRole.objects.filter(permission_groups__isnull=False, role_id=old_role.id).aexists()
            if managed_role:
                User = await sync_to_async(get_user_model)()
                should_have_role = await User.objects.filter(groups__discord_roles__role_id=old_role.id, discorduser__discorduid=after.id).aexists()

                if should_have_role:
                    await after.add_roles(old_role)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload):
        user = payload.user

        discorduser, created = await sync_to_async(DiscordUser.ensure_with_user)(
            discorduid=user.id, access_token=None, refresh_token=None, access_token_expires=None,
        )
        await DiscordUserGuildEvent.objects.acreate(
            kind=DiscordUserGuildEvent.Kind.USER_LEAVE,
            details={"new_discord_user": created, "user": _serialize_discord_user(user)},
            discorduser=discorduser
        )

    async def add_or_update_member(self, member):
        discorduser, created = await sync_to_async(DiscordUser.ensure_with_user)(
            discorduid=member.id,
            access_token=None,
            refresh_token=None,
            access_token_expires=None,
        )
        if created:
            await DiscordUserGuildEvent.objects.acreate(
                kind=DiscordUserGuildEvent.Kind.USER_NEWDETECT,
                details={"member": _serialize_discord_user(member)},
                discorduser=discorduser
            )

        cache_key = "bot.discorduid.{0}".format(member.id)
        data = {
            "username": member.name,
            "membername": member.display_name,
            "discriminator": member.discriminator,
            "avatar": member.display_avatar.url if member.display_avatar else None,
        }
        await sync_to_async(cache.set)(cache_key, data, None)

        return discorduser, created

    async def sync_managed_user_roles(self, backend_user, discord_member):
        current_roles = set([x.id for x in discord_member.roles])
        managed_roles = set([role.role_id async for role in DiscordRole.objects.filter(permission_groups__isnull=False).distinct()])

        should_have_managed_roles = set([role.role_id async for role in DiscordRole.objects.prefetch_related("permission_groups").filter(permission_groups__in=[group async for group in backend_user.groups.all()])])

        add_roles = [discord.Object(id=role) for role in should_have_managed_roles - current_roles]
        rem_roles = [discord.Object(id=role) for role in (managed_roles & current_roles) - should_have_managed_roles]

        if add_roles:
            self.logger.info("sync_managed_user_roles pk=%s add_roles %s", backend_user.pk, add_roles)
            await discord_member.add_roles(*add_roles)
        if rem_roles:
            self.logger.info("sync_managed_user_roles pk=%s rem_roles %s", backend_user.pk, rem_roles)
            await discord_member.remove_roles(*rem_roles)

        self.logger.info("sync_managed_user_roles pk=%s discord=%s", backend_user.pk, discord_member)

    async def sync_display_name(self, backend_user, discord_member):
        new_name_part = await backend_user.aget_setting_value(CustomDisplayName.import_path)

        if not new_name_part:
            self.logger.warning(f"SKIP sync_display_name pk={backend_user.pk}, {discord_member}: User has no CustomDisplayName")
            return

        # Fetch rank safely — it may not be in the cache if user wasn't select_related
        try:
            rank = await self.OrgRank.objects.filter(pk=backend_user.rank_id).afirst() if backend_user.rank_id else None
        except Exception:
            rank = None

        if rank:
            new_name = "{} {}".format(rank.prefix, new_name_part)
        else:
            new_name = new_name_part

        if new_name != discord_member.display_name:
            await discord_member.edit(nick=new_name)

            self.logger.info(f"sync_display_name pk={backend_user.pk}, {discord_member}")

        else:
            self.logger.debug(f"sync_display_name [skipped, user already has correct name] pk={backend_user.pk}, {discord_member}")

    async def on_backend_signal_user_change(self, user_pk):
        raw_guild_id = await self.client.aget_main_guild_id()
        guild = self.client.get_guild(raw_guild_id)

        user = await self.user_model.objects.select_related("discorduser").aget(pk=user_pk)

        try:
            discorduser = user.discorduser
        except Exception:
            self.logger.warning("on_backend_signal_user_change: OrgPlayer pk=%s has no discorduser, skipping", user_pk)
            return

        member = None
        try:
            member = guild.get_member(discorduser.discorduid) or await guild.fetch_member(discorduser.discorduid)
        except discord.NotFound:
            self.logger.info(f"on_backend_signal_user_change guild.fetch_member for discorduid.{discorduser.discorduid}: NotFound")
        except Exception:
            self.logger.exception(f"on_backend_signal_user_change guild.fetch_member for discorduid.{discorduser.discorduid}:")

        if member is None:
            return

        await self.sync_managed_user_roles(user, member)

    async def on_backend_signal_rank_change(self, user_pk, rank_before_pk, rank_after_pk):
        from app.preferences.utils import aget_global_preference
        from app.candidacy.preferences import RankAnnouncementChannelID

        chan_id = await aget_global_preference(RankAnnouncementChannelID.import_path)
        if not chan_id:
            return

        chan = self.client.get_channel(int(chan_id))
        if not chan:
            return

        try:
            user = await self.user_model.objects.select_related("discorduser", "rank").aget(pk=user_pk)
        except Exception:
            self.logger.exception("on_backend_signal_rank_change: failed to fetch user pk=%s", user_pk)
            return

        rank_before = None
        rank_after  = None
        if rank_before_pk:
            rank_before = await self.OrgRank.objects.filter(pk=rank_before_pk).afirst()
        if rank_after_pk:
            rank_after = await self.OrgRank.objects.filter(pk=rank_after_pk).afirst()

        # Determine direction: promotion if after rank pk is higher, else demotion
        is_promotion = bool(
            rank_after_pk and (not rank_before_pk or int(rank_after_pk) > int(rank_before_pk))
        )

        discord_uid = getattr(getattr(user, "discorduser", None), "discorduid", None)
        mention = f"<@{discord_uid}>" if discord_uid else getattr(user, "display_name", f"User #{user_pk}")

        before_str = f"{rank_before.prefix} {rank_before.name}".strip() if rank_before else "None"
        after_str  = f"{rank_after.prefix} {rank_after.name}".strip() if rank_after else "None"

        embed = discord.Embed(
            title="🎖️ Promotion" if is_promotion else "📉 Rank Change",
            color=0x22C55E if is_promotion else 0xEF4444,
            timestamp=discord.utils.utcnow(),
        )
        embed.description = (
            f"{mention} has been {'promoted' if is_promotion else 'updated'}!\n\n"
            f"**{before_str}** → **{after_str}**"
        )
        await chan.send(embed=embed)
        self.logger.info(
            "Rank announcement posted: user_pk=%s %s -> %s",
            user_pk, before_str, after_str,
        )

    async def on_backend_signal_user_name_change(self, user_pk):
        raw_guild_id = await self.client.aget_main_guild_id()
        guild = self.client.get_guild(raw_guild_id)

        user = await self.user_model.objects.prefetch_related("discorduser", "rank").aget(pk=user_pk)

        if not user.discorduser:
            return

        member = None
        try:
            member = guild.get_member(user.discorduser.discorduid) or await guild.fetch_member(user.discorduser.discorduid)
        except discord.NotFound:
            self.logger.info(f"on_backend_signal_user_name_change guild.fetch_member for discorduid.{user.discorduser.discorduid}: NotFound")
        except Exception:
            self.logger.exception(f"on_backend_signal_user_name_change guild.fetch_member for discorduid.{user.discorduser.discorduid}:")

        if member is None:
            return

        await self.sync_display_name(user, member)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        cache_key = "bot.discorduid.{0}".format(member.id)
        await sync_to_async(cache.set)(cache_key, {
            "username": member.name,
            "membername": member.display_name,
            "discriminator": member.discriminator,
            "avatar": member.display_avatar.url if member.display_avatar else None,
        }, None)

        discorduser, created = await self.add_or_update_member(member)
        user = discorduser.user or await database_sync_to_async(get_anonymous_user)()

        await DiscordUserGuildEvent.objects.acreate(
            kind=DiscordUserGuildEvent.Kind.USER_JOIN,
            details={"new_discord_user": created, "member": _serialize_discord_user(member)},
            discorduser=discorduser
        )
        await self.sync_managed_user_roles(user, member)
        await self.sync_display_name(user, member)

    async def rank_autocomplete(self, interaction: discord.Interaction, current: str):
        if current:
            ranks_query = self.OrgRank.objects.filter(Q(title__icontains=current) | Q(description__icontains=current))
        else:
            ranks_query = self.OrgRank.objects.all()

        ranks = [app_commands.Choice(name="{} {}".format(e.prefix, e.name)[:100], value=e.pk) async for e in ranks_query.order_by('order')[:25]]

        return ranks

    @app_commands.command(name="adm_usersetup", description="Set a user up [admin]")
    @app_commands.autocomplete(rank=rank_autocomplete)
    @requires_django_perm("org.can_manage_discord_members")
    async def cmd_usersetup(self, interaction: discord.Interaction, member: discord.Member, rank: int = None, name: str = None):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if member.bot:
            await interaction.followup.send("Error: Selected member is a bot, not supported.")
            return

        actions_taken = []

        if rank is not None:
            try:
                selected_rank = await self.OrgRank.objects.aget(pk=rank)
            except self.OrgRank.DoesNotExist:
                await interaction.followup.send("Error: Rank PK lookup incorrect! Please select a valid rank from the autocomplete menu. Or supply a correct PK directly")
                return

        discorduser, created = await sync_to_async(DiscordUser.ensure_with_user)(discorduid=member.id, access_token=None, refresh_token=None, access_token_expires=None)

        if created:
            actions_taken.append("- Added user to backend")

        await aprefetch_related_objects([discorduser], 'user__rank')
        await discorduser.user.aload_cache()

        custom_display_name = await discorduser.user.aget_setting(CustomDisplayName.import_path)

        if not name:
            if not custom_display_name.value or CustomDisplayName.default_value == custom_display_name.value:
                custom_display_name.value = member.display_name
                await custom_display_name.asave()
                actions_taken.append("- Added backend Custom Display Name based on current Discord Display Name (command used without supplying arg: name)")

        else:
            custom_display_name.value = name
            await custom_display_name.asave()
            actions_taken.append("- Set backend Custom Display Name based on arg: name)")

        custom_display_name_source = await discorduser.user.aget_setting(DisplayNameSource.import_path)

        custom_display_name_source.value = 4
        await custom_display_name_source.asave()
        actions_taken.append("- Set backend Display Name Source to 4 (Rank + Custom Display Name). Command ensures this setting when used.")

        if rank is not None:
            discorduser.user.rank = selected_rank
            await discorduser.user.asave()
            actions_taken.append(f"- Set backend rank to {selected_rank.name} '{selected_rank.pk}' based on arg: rank)")

        await interaction.followup.send("Success, Actions Taken:\n{}".format("\n".join(actions_taken)))


async def setup(client):
    await client.add_cog(UserManagerCog(client))
