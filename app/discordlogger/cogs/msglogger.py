import asyncio
import logging
from datetime import timedelta

import discord
from discord.ext import commands, tasks

from django.utils import timezone
from django.apps import apps
from django.db import IntegrityError

import aiohttp

from app.preferences.utils import aget_global_preference
from ..preferences import MSGLoggerAttachUploadURL, MSGLoggerAttachUploadDuration, DiscordVoiceStateSaveInterval, MSGLoggerExcludedChannelIDs
from ..models import LoggedDiscordMessage, LoggedDiscordChannel, LoggedVoiceState, LoggedVoiceStateSnapshot



class MessageLogger(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.DiscordUser = apps.get_model('discordauth', 'DiscordUser')
        self.startup_task.start()

    @tasks.loop(count=1)
    async def startup_task(self):
        self.logger.info("msglogger startup_task")
        self.voice_logger.start()
        await self.sync_existing_channels()

    @startup_task.before_loop  # Pause untill set up
    async def before_startup_task(self):
        await self.client.wait_until_ready()

    def cog_unload(self):
        self.startup_task.cancel()
        self.voice_logger.cancel()

    async def _is_target_guild(self, guild: discord.Guild) -> bool:
        """Helper to ensure we only log the specified guild."""
        raw_guild_id = await self.client.aget_main_guild_id()
        org_guild = self.client.get_guild(raw_guild_id)

        return guild.id == org_guild.id

    async def _should_log_channel(self, channel: discord.abc.GuildChannel) -> bool:
        raw = await aget_global_preference(MSGLoggerExcludedChannelIDs.import_path)
        if not raw:
            return True
        excluded = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
        if channel.id in excluded:
            return False
        if getattr(channel, "category_id", None) in excluded:
            return False
        return True

    async def stream_upload(self, attachment_url, filename, upload_duration, upload_url):
        """
        Chunked LitterBox Upload from URL, also chucnked doanloads from url to memory
        Returns the new URL or None if failed.
        """

        try:
            async with aiohttp.ClientSession() as session:
                self.logger.info(f"Starting upload for {attachment_url} to Litterbox")
                # 1. Open the download stream from Discord
                async with session.get(attachment_url) as dl_resp:
                    if dl_resp.status != 200:
                        return None

                    data = aiohttp.FormData()
                    data.add_field('reqtype', 'fileupload')
                    data.add_field('time', upload_duration)

                    data.add_field('fileToUpload', dl_resp.content, content_type='application/octet-stream')

                    async with session.post(upload_url, data=data) as ul_resp:
                        if ul_resp.status == 200:
                            text = await ul_resp.text()
                            if text.startswith("https://"):
                                return [text, attachment_url]
                        return None

        except Exception as e:
            self.logger.error(f"Upload error for {filename}: {e}")
            return None

    async def litterboxify_message_attachments(self, message):
        upload_duration = await aget_global_preference(MSGLoggerAttachUploadDuration.import_path)
        upload_url = await aget_global_preference(MSGLoggerAttachUploadURL.import_path)

        tasks = []

        for attachment in message.attachments:
            task = self.stream_upload(attachment.proxy_url, attachment.filename, upload_duration, upload_url)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        r = {r[1]: r[0] for r in results if r}

        return r

    async def log_new_message(self, message, defaults=None):
        defaults = defaults or {}

        litterbox_successes = {}
        if message.attachments:
            litterbox_successes = await self.litterboxify_message_attachments(message)

        log_entry = LoggedDiscordMessage(**defaults)
        log_entry.moderated = LoggedDiscordMessage.Moderated.NO
        log_entry.id = message.id
        log_entry.content = message.content
        log_entry.attachments = [[attachment.proxy_url, litterbox_successes.get(attachment.proxy_url, None), attachment.id] for attachment in message.attachments]
        log_entry.author_name = message.author.display_name
        log_entry.channel_id = message.channel.id
        log_entry.author_id = message.author.id

        #try: we're not enforcing fk integrity anymore
        #    await log_entry.asave(force_insert=True)
        #except IntegrityError:  # The FK constraint failed because the DiscordUser doesn't exist.
        #    await self.DiscordUser.objects.acreate(discorduid=message.author.id)
        #    await log_entry.asave(force_insert=True)

        await log_entry.asave(force_insert=True)

        return log_entry

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if not await self._is_target_guild(message.guild):
            return

        if not await self._should_log_channel(message.channel):
            return

        await self.log_new_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild:
            return

        if not await self._is_target_guild(before.guild):
            return

        if not await self._should_log_channel(before.channel):
            return

        if before.content == after.content and before.attachments == after.attachments:
            return

        try:
            log_entry = await LoggedDiscordMessage.objects.aget(id=before.id)
            log_entry.event = LoggedDiscordMessage.Event.EDIT

            if before.content != after.content:
                log_entry.content = after.content
                log_entry.moderated = LoggedDiscordMessage.Moderated.NO

            if before.attachments != after.attachments:  # can only remove
                attach_filter = [a.id for a in after.attachments]

                new_attachlist = []
                for entry in log_entry.attachments:
                    if entry[2] in attach_filter:
                        new_attachlist.append(entry)

                log_entry.attachments = new_attachlist
                log_entry.moderated = LoggedDiscordMessage.Moderated.UNSURE

            await log_entry.asave()

        except LoggedDiscordMessage.DoesNotExist:
            await self.log_new_message(after, defaults={"event": LoggedDiscordMessage.Event.EDIT})

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if not await self._is_target_guild(message.guild):
            return

        if not await self._should_log_channel(message.channel):
            return

        discord_executor = None
        async for entry in message.guild.audit_logs(limit=4, action=discord.AuditLogAction.message_delete):
            if entry.target.id == message.author.id and entry.extra.channel.id == message.channel.id:
                discord_executor, _ = await self.DiscordUser.objects.aget_or_create(discorduid=entry.user.id)
                break

        try:
            log_entry = await LoggedDiscordMessage.objects.aget(id=message.id)
        except LoggedDiscordMessage.DoesNotExist:
            log_entry = await self.log_new_message(message, defaults={
                "event": LoggedDiscordMessage.Event.DELETE,
                "moderated": LoggedDiscordMessage.Moderated.YES if discord_executor else LoggedDiscordMessage.Moderated.UNSURE,
                "discord_executor": discord_executor,
            })
        else:
            log_entry.event = LoggedDiscordMessage.Event.DELETE
            log_entry.moderated = LoggedDiscordMessage.Moderated.YES if discord_executor else LoggedDiscordMessage.Moderated.UNSURE
            log_entry.discord_executor = discord_executor

            await log_entry.asave()

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):
        if not messages:
            return

        if not await self._is_target_guild(messages[0].guild):
            return

        if not await self._should_log_channel(messages[0].channel):
            return

        now = timezone.now()
        discord_executor = None

        async for entry in messages[0].guild.audit_logs(action=discord.AuditLogAction.message_bulk_delete, limit=1):
            if (now - entry.created_at).total_seconds() < 14:
                discord_executor, _ = await self.DiscordUser.objects.aget_or_create(discorduid=entry.user.id)
                break

        for message in messages:
            if message.author.bot or not message.guild:
                continue

            try:
                log_entry = await LoggedDiscordMessage.objects.aget(id=message.id)
            except LoggedDiscordMessage.DoesNotExist:
                log_entry = await self.log_new_message(message, defaults={
                    "event": LoggedDiscordMessage.Event.DELETE,
                    "moderated": LoggedDiscordMessage.Moderated.YES,
                    "discord_executor": discord_executor,
                })
            else:
                log_entry.event = LoggedDiscordMessage.Event.DELETE
                log_entry.moderated = LoggedDiscordMessage.Moderated.YES
                log_entry.discord_executor = discord_executor

                await log_entry.asave()

    async def sync_existing_channels(self):
        self.logger.info("Starting full Channel sync...")
        await self.client.wait_until_ready()

        try:
            raw_guild_id = await self.client.aget_main_guild_id()
            guild = self.client.get_guild(raw_guild_id)

            if not guild:
                self.logger.warning("MessageLogger.sync_existing_channels: Target guild not found in cache.")
                return

            # guild.channels includes Text, Voice, Categories, Forums, etc.
            channels_created = 0
            for channel in guild.channels:
                # Use aget_or_create so it only creates if it doesn't already exist
                _, created = await LoggedDiscordChannel.objects.aget_or_create(
                    id=channel.id,
                    defaults={
                        "name": channel.name,
                        "position": channel.position,
                        "channel_type": str(channel.type)
                    }
                )
                if created:
                    channels_created += 1

            # Sync existing active threads as well
            for thread in guild.threads:
                _, created = await LoggedDiscordChannel.objects.aget_or_create(
                    id=thread.id,
                    defaults={
                        "name": thread.name,
                        "parent_id": thread.parent_id,
                        "channel_type": str(thread.type)
                    }
                )
                if created:
                    channels_created += 1

            self.logger.info(f"Channel sync complete. Added {channels_created} new channels/threads to the DB.")

        except Exception as e:
            self.logger.exception(f"MessageLogger.sync_existing_channels: Error syncing channels: {e}")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not await self._is_target_guild(channel.guild):
            return

        ch = LoggedDiscordChannel(
            id=channel.id,
            name=channel.name,
            position=channel.position,
            channel_type=str(channel.type)
        )
        await ch.asave(force_insert=True)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if not await self._is_target_guild(after.guild):
            return

        if before.name != after.name or before.position != after.position:
            try:
                # Fetching asynchronously
                ch = await LoggedDiscordChannel.objects.aget(id=after.id)
                ch.name = after.name
                ch.position = after.position
                # Saving asynchronously.
                # Note: We must use asave() instead of aupdate() because aupdate()
                # bypasses the post_save signals that django-simple-history relies on.
                await ch.asave()
            except LoggedDiscordChannel.DoesNotExist:
                pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not await self._is_target_guild(channel.guild):
            return

        try:
            ch = await LoggedDiscordChannel.objects.aget(id=channel.id)
            ch.deleted_at = timezone.now()
            await ch.asave()
        except LoggedDiscordChannel.DoesNotExist:
            pass

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if not await self._is_target_guild(thread.guild):
            return

        ch = LoggedDiscordChannel(
            id=thread.id,
            name=thread.name,
            parent_id=thread.parent_id,
            channel_type=str(thread.type)
        )
        await ch.asave(force_insert=True)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if not await self._is_target_guild(after.guild):
            return

        if before.name != after.name:
            try:
                ch = await LoggedDiscordChannel.objects.aget(id=after.id)
                ch.name = after.name
                await ch.asave()
            except LoggedDiscordChannel.DoesNotExist:
                pass

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        if not await self._is_target_guild(thread.guild):
            return

        try:
            ch = await LoggedDiscordChannel.objects.aget(id=thread.id)
            ch.deleted_at = timezone.now()
            await ch.asave()
        except LoggedDiscordChannel.DoesNotExist:
            pass

    @tasks.loop(seconds=1)
    async def voice_logger(self):
        """Polls all active voice connections in the target guild every x seconds."""

        try:
            raw_guild_id = await self.client.aget_main_guild_id()
            guild = self.client.get_guild(raw_guild_id)

            if not guild:
                self.logger.warning(f"MessageLogger.voice_logger not guild, skipping.")

                new_interval = timedelta(seconds=await aget_global_preference(DiscordVoiceStateSaveInterval.import_path))
                self.voice_logger.change_interval(seconds=new_interval.seconds)

                return

            logs_to_create = []

            for voice_channel in guild.voice_channels:
                for member in voice_channel.members:
                    voice_state = member.voice
                    if not voice_state:
                        continue

                    logs_to_create.append(
                        LoggedVoiceState(
                            discorduser_id=member.id,
                            channel_id=voice_channel.id,
                            self_mute=voice_state.self_mute,
                            self_deaf=voice_state.self_deaf,
                            server_mute=voice_state.mute,
                            server_deaf=voice_state.deaf,
                            self_stream=voice_state.self_stream,
                            self_video=voice_state.self_video,
                        )
                    )

            new_interval = timedelta(seconds=await aget_global_preference(DiscordVoiceStateSaveInterval.import_path))
            self.voice_logger.change_interval(seconds=new_interval.seconds)

            snapshot = LoggedVoiceStateSnapshot(expected_resolution=new_interval)
            await snapshot.asave()

            if logs_to_create:
                for log in logs_to_create:
                    log.for_snapshot = snapshot
                await LoggedVoiceState.objects.abulk_create(logs_to_create)

        except Exception as e:
            self.logger.exception(f"MessageLogger.voice_logger: Error: {e}")

    @voice_logger.before_loop
    async def before_voice_logger(self):
        await self.client.wait_until_ready()


async def setup(client):
    await client.add_cog(MessageLogger(client))
