import logging

import discord
from discord.ext import commands, tasks
from django.core.exceptions import ObjectDoesNotExist
from django.apps import apps

from app.preferences.utils import aget_global_preference


class PrivNotificationCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.PrivateNotifyThread = apps.get_model('org', 'PrivateNotifyThread')

        self.startup_task.start()

    def cog_unload(self):
        self.startup_task.cancel()

    @tasks.loop(count=1)
    async def startup_task(self):
        from app.org.preferences import PrivNotifyParentChannelID
        chan_id = await aget_global_preference(PrivNotifyParentChannelID.import_path)
        self.parent_chan = self.client.get_channel(chan_id) if chan_id else None

    @startup_task.before_loop
    async def before_startup_task(self):
        await self.client.wait_until_ready()

    async def get_or_create_thread(self, user: discord.User):
        """
        Retrieves an existing thread for the user from the DB or creates a new one.
        Handles cases where the thread in DB was manually deleted from Discord.
        """
        try:
            record = await self.PrivateNotifyThread.objects.aget(user_id=user.id)

            try:
                thread = self.client.get_channel(record.thread_id) or await self.client.fetch_channel(record.thread_id)
                if isinstance(thread, discord.Thread):
                    return thread
                else:
                    self.logger.error(f"Failed to get private notification thread {record.thread_id} for {user.id}: instance is not a thread.")
            except discord.NotFound:
                await record.adelete()
        except ObjectDoesNotExist:
            pass

        try:
            thread = await self.parent_chan.create_thread(name=f"{user.display_name}'s Notifications", type=discord.ChannelType.private_thread, invitable=False, auto_archive_duration=60)

            await thread.add_user(user)

            await self.PrivateNotifyThread.objects.acreate(user_id=user.id, thread_id=thread.id)

            return thread
        except discord.Forbidden:
            self.logger.error(f"Missing permissions to create threads in ({self.parent_chan.name}, {self.parent_chan.id})")
            return None

    async def send_private_notification(self, user: discord.User, content: str = None, **kwargs):
        """
        Sends a private message to the user's persistent thread.
        Unarchives it briefly to send, then re-archives/locks it.
        """
        try:
            thread = await self.get_or_create_thread(user)
            if not thread:
                return False

            await thread.send(f"{content}\n-# {user.mention}" if content else f"-# {user.mention}", **kwargs)

            await thread.edit(locked=True, archived=False)

            return True

        except Exception as e:
            self.logger.error(f"Failed to send private notification to {user.id}: {e}")
            return False


async def setup(client):
    await client.add_cog(PrivNotificationCog(client))
