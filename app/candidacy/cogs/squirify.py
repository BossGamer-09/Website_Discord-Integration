import asyncio
import random
import logging
import json
from typing import Literal, Optional

import discord
from discord.ext import commands, tasks
from discord import app_commands

from django.apps import apps
from django.conf import settings
from asgiref.sync import sync_to_async

import redis.asyncio as redis

from app.main.util.db import get_groups_with_permission_on, get_users_with_permission_on
from app.main.util.discord_command_checks import requires_django_perm, requires_django_object_perm_by_arg, requires_django_object_perm_by_interaction_attr, requires_django_object_perm_by_arg_or_interaction_attr
from app.main.util.discord_command_checks import MissingDjangoPermission, MissingDjangoObjectPermission, AccountNotLinked, UnregisteredObjectError
from app.preferences.utils import aget_global_preference
from ..preferences import SquirifyDiscordChanID, SquirifyKnightRankPK
from django.contrib.auth import get_user_model


EMBED_COLOR = discord.Color.red()



class SquirifyCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.SquireTrialThread = apps.get_model('candidacy', 'SquireTrialThread')
        self.User = get_user_model()
        self.startup_task.start()

    async def cog_unload(self):
        self.startup_task.cancel()
        self.sync_listener_loop.cancel()
        try:
            if self.pubsub:
                await self.pubsub.unsubscribe()
        except Exception:
            pass
        try:
            await self.redis_client.aclose()
        except Exception:
            pass

    # Redis Listener:
    @tasks.loop(seconds=0.1)
    async def sync_listener_loop(self):
        from app.unifieduser import signals
        if not self.pubsub.subscribed:
            await self.pubsub.subscribe(signals.USER_PERMISSION_PUBSUB_CHANNEL)

        try:
            message = await self.pubsub.get_message(ignore_subscribe_messages=True)
            if message:
                data = json.loads(message['data'])
                await self.process_event(data)
        except Exception:
            self.logger.exception("SquirifyCog Sync Error:")

    async def process_event(self, data):
        action = data.get('action')
        obj_type = data.get('type')

        if obj_type != "User.Permission":
            return

        if data.get('permission') == "candidacy.can_feedback_squiretrialthread":
            user_id = data.get('user_id')

            try:
                user = await self.User.objects.prefetch_related("discorduser").aget(pk=user_id)
                discord_id = user.discorduser.discorduid
            except (self.User.DoesNotExist, AttributeError):
                self.logger.warning(f"User {user_id} not found or not linked to Discord.")
                return

            member = await self.get_member_by_id(discord_id)

            if action == "added":
                if obj_id := data.get("obj_id", None):
                    thread = await self.get_thread_by_id(obj_id)
                    await self.add_user_to_thread(member, thread)
                else:
                    items = await self.get_active_squire_trials()
                    for thread, trial in items:
                        await self.add_user_to_thread(member, thread)

            elif action == "removed":
                if obj_id := data.get("obj_id", None):
                    thread = await self.get_thread_by_id(obj_id)
                    await self.remove_user_from_thread(member, thread)
                else:
                    items = await self.get_active_squire_trials()
                    for thread, trial in items:
                        if not await user.ahas_perm("candidacy.can_feedback_squiretrialthread", trial):
                            await self.remove_user_from_thread(member, thread)

    @commands.Cog.listener()
    async def on_ready(self):
        pass

    async def get_member_by_id(self, user_id):
        guild = self.parent_chan.guild

        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except discord.NotFound:
            self.logger.error(f"User {user_id} is not in Guild ID {guild.id}, cannot fetch member.")
            raise
        except discord.Forbidden:
            self.logger.error(f"Bot missing permission to access members.")
            raise

        return member

    async def get_thread_by_id(self, thread_id):
        thread = self.client.get_channel(thread_id)

        if thread is None:  # possible for old/inactive threads
            try:
                thread = await self.client.fetch_channel(thread_id)
            except discord.NotFound:
                self.logger.error(f"Thread ID {thread_id} not found.")
                raise
            except discord.Forbidden:
                self.logger.error(f"Bot missing access to thread {thread_id}.")
                raise
            if not isinstance(thread, discord.Thread):
                self.logger.warning(f"ID {thread_id} is not a Thread.")
                raise ValueError

        return thread

    def str_strip_rank(self, display_name):
        return display_name.split("]", 1)[-1].lstrip()

    async def _start_squire_trial(self, squire_user_id, initiator_user_id=None):
        existing = await self.SquireTrialThread.objects.filter(
            squire_id=squire_user_id, status=self.SquireTrialThread.Status.ACTIVE
        ).aexists()
        if existing:
            raise ValueError("active_trial_exists")

        squire = await self.get_member_by_id(squire_user_id)
        initiator = await self.get_member_by_id(initiator_user_id) if initiator_user_id else None
        squire_rankless_name = self.str_strip_rank(squire.display_name)

        thread = await self.parent_chan.create_thread(
            name=f"Squire Feedback: {squire_rankless_name}",
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason="Bot: Squire Trial Thread",
            auto_archive_duration=10080,
        )

        trial = self.SquireTrialThread(thread_id=thread.id, squire_id=squire.id, initiator_id=initiator.id if initiator else None, status=self.SquireTrialThread.Status.ACTIVE)
        await trial.asave()

        roles_to_mention = "".join("<@&{}>".format(role_id) for role_id in await self.aget_access_role_ids(obj=trial))
        users_to_mention = "".join("<@{}>".format(user_id) for user_id in await self.aget_access_discord_ids(obj=trial, include_from_groups=False))

        await thread.send("-# {}{}\n## Feel free to share your personal feedback on {} ({}) and how they fit and are progressing into **Knights**.\n> Please keep it on-topic!".format(roles_to_mention, users_to_mention, squire.mention, squire_rankless_name))

        return thread, trial

    async def _finalize_squire_trial(self, thread_id, outcome_choice, actor_discord_id=None):
        if outcome_choice not in (self.SquireTrialThread.Status.SUCCESS, self.SquireTrialThread.Status.DENIED,):
            raise ValueError

        try:
            obj = await self.SquireTrialThread.objects.filter(thread_id=thread_id).aget()
        except self.SquireTrialThread.DoesNotExist:
            self.logger.exception(f"Cannot finalize trial: Thread ID {thread_id} not found in DB.\n")
            raise

        thread = await self.get_thread_by_id(thread_id)
        squire = await self.get_member_by_id(obj.squire_id)

        obj.status = outcome_choice

        if outcome_choice == self.SquireTrialThread.Status.SUCCESS:
            await self._promote_to_knight(obj, squire, actor_discord_id)
            msg = await self.parent_chan.send(f"# {squire.mention} Has Ascended to Knighthood!\nWelcome and congratulations!🤩")
            await thread.send(f"Finalized, {obj.get_status_display()}: {msg.jump_url}")
        elif outcome_choice == self.SquireTrialThread.Status.DENIED:
            await thread.send(f"Finalized, {obj.get_status_display()}")

        await asyncio.gather(thread.edit(archived=True, locked=True), obj.asave())

    async def _promote_to_knight(self, trial_obj, squire_member: discord.Member, actor_discord_id=None):
        """Set Knight rank on OrgPlayer and write a DecisionLog entry."""
        rank_pk = await aget_global_preference(SquirifyKnightRankPK.import_path)
        if not rank_pk:
            self.logger.warning("SquirifyKnightRankPK not configured — skipping auto-promotion.")
            return

        from django.apps import apps as django_apps
        from app.leadership.models import DecisionLog
        from asgiref.sync import sync_to_async

        OrgRank = django_apps.get_model('unifieduser', 'OrgRank')
        new_rank = await OrgRank.objects.filter(pk=rank_pk).afirst()
        if not new_rank:
            self.logger.error(f"SquirifyKnightRankPK={rank_pk} not found in OrgRank — skipping auto-promotion.")
            return

        subject = await self.User.objects.filter(discorduser__discorduid=squire_member.id).afirst()
        if not subject:
            self.logger.error(f"No OrgPlayer linked to Discord ID {squire_member.id} — cannot auto-promote.")
            return

        old_rank = await sync_to_async(lambda: subject.rank)()
        old_rank_str = f'{old_rank.prefix} {old_rank.name}'.strip() if old_rank else 'None'
        new_rank_str = f'{new_rank.prefix} {new_rank.name}'.strip()

        await subject.aset_rank(rank=new_rank)

        actor = None
        if actor_discord_id:
            actor = await self.User.objects.filter(discorduser__discorduid=actor_discord_id).afirst()

        await DecisionLog.objects.acreate(
            event_type=DecisionLog.EventType.PROMOTION,
            subject=subject,
            actor=actor,
            summary=f'Promoted via Squire Trial acceptance: {old_rank_str} → {new_rank_str}',
            rank_before_id=old_rank.pk if old_rank else None,
            rank_after_id=new_rank.pk,
        )
        self.logger.info(f"Auto-promoted {squire_member} to {new_rank_str} via squire trial.")

    async def get_active_squire_trials(self):
        """Helper to fetch actual Thread objects from IDs."""
        r = []

        async for trial in self.SquireTrialThread.objects.filter(closed_at__isnull=True):
            try:
                thread = await self.get_thread_by_id(trial.thread_id)
            except Exception as exc:
                continue
            r.append([thread, trial])

        return r

    def get_access_role_ids(self, obj=None):
        """Check if a member has at least one of the watched roles."""
        permission_groups = get_groups_with_permission_on("candidacy.can_feedback_squiretrialthread", obj)
        role_ids = permission_groups.exclude(discord_roles__isnull=True).values_list('discord_roles__role_id', flat=True)
        return role_ids

    def get_access_discord_ids(self, obj=None, include_from_groups=False):
        users = get_users_with_permission_on("candidacy.can_feedback_squiretrialthread", obj, include_from_groups=include_from_groups)
        discord_ids = users.exclude(discorduser__isnull=True).values_list('discorduser__discorduid', flat=True)
        return discord_ids

    @sync_to_async
    def aget_access_role_ids(self, obj=None):
        return self.get_access_role_ids(obj)

    @sync_to_async
    def aget_access_discord_ids(self, obj=None, include_from_groups=False):
        return self.get_access_discord_ids(obj, include_from_groups)

    async def add_user_to_thread(self, member: discord.Member, thread: discord.Thread):
        try:
            # Check d.py cache to avoid API spam
            user_in_thread = any(tm.id == member.id for tm in thread.members)
            if not user_in_thread:
                await thread.add_user(member)
        except discord.Forbidden:
            self.logger.error(f"Missing permissions to add user to thread {thread.name}.")
        except Exception as e:
            self.logger.error(f"Failed to add {member} to {thread.name}: {e}")

    async def remove_user_from_thread(self, member: discord.Member, thread: discord.Thread):
        try:
            # Check d.py cache to avoid API spam
            user_in_thread = any(tm.id == member.id for tm in thread.members)
            if user_in_thread:
                await thread.remove_user(member)
        except discord.Forbidden:
            self.logger.error(f"Missing permissions to remove user from thread {thread.name}.")
        except Exception as e:
            self.logger.error(f"Failed to remove {member} from {thread.name}: {e}")

    async def setup_main_channel(self):
        self.logger.info("Setting up main channel...")
        channel_id = await aget_global_preference(SquirifyDiscordChanID.import_path)
        self.parent_chan = self.client.get_channel(channel_id)

    async def sync_squire_trial_threads(self):
        """
        Runs once when the bot starts (or cog loads).
        Rectifies state between Roles and Threads.
        """
        self.logger.info("Starting Squire Thread Access Rectification...")
        items = await self.get_active_squire_trials()
        if not items:
            self.logger.info("No valid threads found to sync.")
            return

        # 1. Identify who SHOULD be in the threads
        # We look at the guild of the first thread (assuming all threads/roles are in same guild)
        guild = self.parent_chan.guild


        # Iterate through each target thread and sync
        for thread, trial in items:
            authorized_member_ids = set(await self.get_access_discord_ids(obj=trial, include_from_groups=True))

            try:
                # We must fetch members to know who is currently in the private thread
                current_thread_members = await thread.fetch_members()
                current_member_ids = {m.id for m in current_thread_members}

                # Filter out the bot itself from removal logic
                current_member_ids.discard(self.client.user.id)

                # Determine actions
                to_add = authorized_member_ids - current_member_ids
                to_remove = current_member_ids - authorized_member_ids

                # Batch Add
                for user_id in to_add:
                    member = guild.get_member(user_id)
                    if member:
                        try:
                            await self.add_user_to_thread(member, thread)
                            self.logger.info(f"Rectify: Added {member} to {thread.name}")
                        except discord.HTTPException:
                            pass # Rate limits or privacy settings

                # Batch Remove
                for user_id in to_remove:
                    member = guild.get_member(user_id)
                    # If member left guild, we can't remove them from thread object easily 
                    # without the member object, but they are gone anyway.
                    if member:
                        try:
                            await self.remove_user_from_thread(member, thread)
                            self.logger.info(f"Rectify: Removed {member} from {thread.name}")
                        except discord.HTTPException:
                            pass

            except Exception as e:
                self.logger.error(f"Error syncing thread {thread.id}: {e}")

        self.logger.info("Thread Access Rectification Complete.")

    @tasks.loop(count=1)
    async def startup_task(self):
        redis_url = settings.CACHES['default']['LOCATION']
        pool = redis.ConnectionPool.from_url(redis_url)
        self.redis_client = redis.Redis.from_pool(pool)

        self.pubsub = self.redis_client.pubsub()
        self.sync_listener_loop.start()

        await self.setup_main_channel()
        await self.sync_squire_trial_threads()

    @startup_task.before_loop
    async def before_startup_task(self):
        # Pause till set up
        await self.client.wait_until_ready()

    @app_commands.command(name="adm_squire_trial_finalize", description="Finalize (close) A Squire Trial [admin]")
    @app_commands.describe(action="Accept or Deny into Knights", target_thread="Select a Squire Trial Thread or use CMD in one.")
    @requires_django_object_perm_by_arg_or_interaction_attr("candidacy.can_mod_squiretrialthread", "target_thread", "channel.id", "candidacy.SquireTrialThread")
    async def cmd_finalize_squire_trial(
        self,
        interaction: discord.Interaction,
        # 1. First Arg: Literal creates a fixed dropdown menu in Discord
        action: Literal['Accept', 'Deny'],
        # 2. Second Arg: Optional[discord.Thread] allows a thread or None
        target_thread: Optional[discord.Thread] = None
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not target_thread:
            if not isinstance(interaction.channel, discord.Thread):
                await interaction.followup.send(f"❌ Specify a Squire thread or use in Squire thread {interaction.channel.mention} is not a valid Squire Thread.", ephemeral=True)
                return
            target_thread = interaction.channel

        try:
            trial_obj = await self.SquireTrialThread.objects.aget(thread_id=target_thread.id)
            from app.main.util.discord_command_checks import _verify_and_cache_permission
            await _verify_and_cache_permission(interaction.user.id, "candidacy.can_mod_squiretrialthread", trial_obj)
        except self.SquireTrialThread.DoesNotExist:
            await interaction.followup.send(f"❌ {target_thread.mention} is not a tracked Squire Trial thread.", ephemeral=True)
            return

        if action == 'Accept':
            outcome_choice = self.SquireTrialThread.Status.SUCCESS
        elif action == 'Deny':
            outcome_choice = self.SquireTrialThread.Status.DENIED

        try:
            await self._finalize_squire_trial(target_thread.id, outcome_choice, actor_discord_id=interaction.user.id)
        except (self.SquireTrialThread.DoesNotExist, ValueError):
            await interaction.followup.send(f"❌ Specify a Squire thread or use in Squire thread {interaction.channel.mention} is not a valid Squire Thread.", ephemeral=True)

        await interaction.followup.send("✅ Completed", ephemeral=True)

    @app_commands.command(name="adm_squire_trial_start", description="Start A Squire Trial for a Member [admin]")
    @requires_django_perm("candidacy.can_mod_squiretrialthread")
    async def cmd_start_squire_trial(self, interaction: discord.Interaction, target_member: discord.Member):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if target_member.bot:
            await interaction.followup.send("❌ This command is not intended to target bots.", ephemeral=True)
            return

        try:
            thread, trial = await self._start_squire_trial(target_member.id, initiator_user_id=interaction.user.id)
        except ValueError as e:
            if str(e) == "active_trial_exists":
                await interaction.followup.send(f"❌ {target_member.mention} already has an active Squire Trial.", ephemeral=True)
                return
            raise

        await interaction.followup.send(f"✅ **{target_member.mention}** is now in Squire Trial, thread: {thread.mention}", ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingDjangoObjectPermission):
            await interaction.response.send_message(
                f"🛡️ **Command Rejected:** You don't have the '{error.missing_perm}' permission for {error.obj_name}.", ephemeral=True
            )

        elif isinstance(error, MissingDjangoPermission):
            await interaction.response.send_message(
                f"🛡️ **Command Rejected:** You don't have the '{error.missing_perm}' permission.", ephemeral=True
            )

        elif isinstance(error, AccountNotLinked):
            await interaction.response.send_message(
                "🎮 You need to link your Discord account to a User Profile first!", ephemeral=True
            )

        else:
            if interaction.response.is_done():
                await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)

            self.logger.error(f"{self.__class__.__name__}.cog_app_command_error: {error}")


async def setup(client):
    await client.add_cog(SquirifyCog(client))
