import os
import json
import asyncio
import redis.asyncio as redis
import discord
import logging
from discord import app_commands
from discord.ext import commands, tasks
from django.conf import settings
from asgiref.sync import sync_to_async

from app.preferences.utils import aget_global_preference
from ..preferences import CMSDiscordForumChanID
from app.main.util.discord_command_checks import requires_django_perm

from ..models import Post, Entry, Tag

# TODO: process reordering

class CMSSyncCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.redis_client = None
        self.pubsub = None

    async def cog_load(self):
        self.channel_id = await aget_global_preference(CMSDiscordForumChanID.import_path)

        redis_url = settings.CACHES['default']['LOCATION']
        pool = redis.ConnectionPool.from_url(redis_url)
        self.redis_client = redis.Redis.from_pool(pool)

        self.pubsub = self.redis_client.pubsub()

        # 1. Start the listener loop
        self.sync_listener_loop.start()

        # 2. Perform a full sync on startup
        self.logger.info("Starting full CMS sync...")
        await self.full_sync()

    async def cog_unload(self):
        self.sync_listener_loop.cancel()
        try:
            await self.redis_client.aclose()
        except Exception:
            pass

    async def full_sync(self):
        await self.sync_tags()

        async for pid in Post.objects.values_list('id', flat=True):
            await self.sync_post(pid)
            await asyncio.sleep(2)  # Rate limits

        self.logger.info("Full sync complete.")

    # Redis Listener:
    @tasks.loop(seconds=0.1)
    async def sync_listener_loop(self):
        from .. import signals
        if not self.pubsub.subscribed:
            await self.pubsub.subscribe(signals.PUBSUB_CHANNEL)

        try:
            message = await self.pubsub.get_message(ignore_subscribe_messages=True)
            if message:
                data = json.loads(message['data'])
                await self.process_event(data)
        except Exception:
            self.logger.exception("CMS Sync Error:")

    async def process_event(self, data):
        action = data.get('action')
        obj_type = data.get('type')

        if obj_type == "TAGS":
            if action == "UPDATE":
                await self.sync_tags()
            else:
                self.logger.debug("Unhandled TAGS action: %s", action)

        elif obj_type == "POST":
            if action == "DELETE":
                discord_id = data.get('discord_id')
                if discord_id:
                    await self.delete_thread(discord_id)
            elif action == "INTERNALREORDER":
                await self.sync_post(data.get('id'))
            elif action in ["CREATE", "UPDATE"]:
                await self.sync_post(data.get('id'))
            else:
                self.logger.debug("Unhandled POST action: %s", action)

        elif obj_type == "ENTRY":
            if action in ["CREATE", "UPDATE"]:
                await self.sync_entry(data.get('id'))
            elif action == "DELETE":
                await self.delete_message(data.get('thread_id'), data.get('discord_id'))
            else:
                self.logger.debug("Unhandled ENTRY action: %s", action)

        elif obj_type == "LIVE_MSG":
            if action == "UPDATE":
                await self.update_live_message(
                    data.get('post_id'),
                    data.get('channel_id'),
                    data.get('message_id'),
                )

    async def delete_thread(self, thread_id):
        try:
            thread = self.client.get_channel(thread_id) or await self.client.fetch_channel(thread_id)
            await thread.delete()

            self.logger.info(f"Deleted thread {thread_id}")

        except discord.NotFound:
            self.logger.warning(f"Delete thread {thread_id}, already deleted")
        except discord.Forbidden:
            self.logger.error(f"Missing permissions to delete thread {thread_id}")
        except discord.HTTPException:
            self.logger.exception(f"HTTPException while deleting thread {thread_id}:")

    async def delete_message(self, thread_id, message_id):
        try:
            thread = self.client.get_channel(thread_id) or await self.client.fetch_channel(thread_id)
            message = thread.get_partial_message(message_id)

            if message_id == thread_id:
                await message.edit(content="<Empty>", embeds=[], attachments=[], view=None)
                self.logger.info(f"Deleted message {message_id}, to empty")
            else:
                await message.delete()
                self.logger.info(f"Deleted message {message_id}")

        except discord.NotFound:
            self.logger.warning(f"Delete message {message_id}, already deleted")
        except discord.Forbidden:
            self.logger.error(f"Missing permissions to delete/edit message {message_id}")
        except discord.HTTPException:
            self.logger.exception(f"HTTPException while deleting message {message_id}:")

    async def sync_tags(self):
        forum_channel = self.client.get_channel(self.channel_id)
        if not forum_channel:
            return

        django_tags = [t async for t in Tag.objects.all().order_by('order')[:20]]
        new_tags = [discord.ForumTag(name=t.name, emoji=None, moderated=False) for t in django_tags]

        try:
            # overwrites existing tags
            await forum_channel.edit(available_tags=new_tags)
        except discord.HTTPException as e:
            self.logger.info(f"Tag Sync Error: {e}")

    async def sync_entry(self, entry_id):
        forum_channel = self.client.get_channel(self.channel_id)
        if not forum_channel:
            return

        try:
            entry = await Entry.objects.prefetch_related("post").aget(id=entry_id)
        except Entry.DoesNotExist:
            return

        thread = forum_channel.get_thread(entry.post.discord_thread_id) or await forum_channel.guild.fetch_channel(entry.post.discord_thread_id)

        try:
            message = await thread.fetch_message(entry.discord_message_id)
        except discord.NotFound:
            message = None

        if message:
            content, f = await self.format_entry(entry)
            await message.edit(content=content, attachments=[f] if f else [])
        else:
            if await sync_to_async(entry.next)():  # new msg and not inserted last, needs full sync
                await self.sync_post(entry.post.id)

            content, f = await self.format_entry(entry)
            message = await thread.send(content=content, file=f)
            entry.discord_message_id = message.id
            await entry.asave()

    async def sync_post(self, post_id):
        forum_channel = self.client.get_channel(self.channel_id)
        if not forum_channel:
            return

        try:
            post = await Post.objects.aget(id=post_id)
        except Post.DoesNotExist:
            return
        else:
            post._skip_signals = True
            post._skip_version = True
            entries = []
            async for entry in post.entries.all().order_by('order'):
                entry._skip_signals = True
                entries.append(entry)
            tags = []
            async for t in post.tags.all():
                t._skip_signals = True
                tags.append(t)

        applied_tags = []
        if tags:
            valid_names = {t.name for t in tags}
            for ft in forum_channel.available_tags:
                if ft.name in valid_names:
                    applied_tags.append(ft)
            applied_tags = applied_tags[:5]

        thread = None
        if post.discord_thread_id:
            try:
                thread = forum_channel.get_thread(post.discord_thread_id) or await forum_channel.guild.fetch_channel(post.discord_thread_id)
                needs_edit = thread.name != post.title or thread.applied_tags != applied_tags or not thread.locked
                if needs_edit:
                    await thread.edit(name=post.title, applied_tags=applied_tags, locked=True)
            except discord.NotFound:
                post.discord_thread_id = None

        first_msg_id = None
        if not post.discord_thread_id:
            initial_content, f = await self.format_entry(entries[0]) if entries else ["<Empty>", None]

            create_thread_kwargs = {
                "name": post.title,
                "content": initial_content,
                "applied_tags": applied_tags
            }

            if f:
                create_thread_kwargs["file"] = f

            thread_payload = await forum_channel.create_thread(**create_thread_kwargs)
            thread, first_message = thread_payload
            await thread.edit(locked=True)

            first_entry = entries[0] if entries else None
            first_msg_id = first_message.id

            post.discord_thread_id = thread.id
            await post.asave()

            if first_entry and first_entry.discord_message_id != first_msg_id:
                first_entry.discord_message_id = first_msg_id
                await first_entry.asave()

        ientries = iter(entries)  # iter down the list of backend and discord messages and sync them
        async for message in thread.history(limit=500, oldest_first=True):
            if message.is_system():
                continue

            if self.client.user != message.author:
                continue  # skip it TODO: maybe make this a setting where instead bot deletes/reports as bot msg?

            entry = next(ientries, None)

            if first_msg_id == message.id: # don't update the first entry if we just created it
                continue

            if not entry:  # a post got deleted in backend now there are too many msges
                if message.id == thread.id: # starter message has same id as thread and we preserve it
                    await message.edit(content="<Empty>", embeds=[], attachments=[], view=None)
                else:
                    await message.delete()
                continue
            
            expected_content, expected_f = await self.format_entry(entry)
            update_attach, update_cont = False, False

            if entry.discord_message_id and entry.discord_message_id != message.id:  # messages got reordered, fuill update msg and id in backend
                update_attach, update_cont = True, True
            else:
                if message.content != expected_content:
                    update_cont = True

                if (message.attachments and message.attachments[0].id != entry.discord_message_attachment_id) or len(message.attachments) > 1 or ((not message.attachments) and entry.entry_type in [Entry.EntryType.IMAGE, Entry.EntryType.FILE]):
                    update_attach = True

            if update_attach and update_cont:
                message = await message.edit(content=expected_content, attachments=[expected_f] if expected_f else [])
            elif update_cont:
                message = await message.edit(content=expected_content)
            elif update_attach:
                message = await message.edit(attachments=[expected_f] if expected_f else [])

            entry.discord_message_id = message.id
            entry.discord_message_attachment_id = message.attachments[0].id if expected_f else None
            await entry.asave()

        while True:
            entry = next(ientries, None)
            if not entry:
                break

            expected_content, expected_f = await self.format_entry(entry)
            message = await thread.send(content=expected_content, file=expected_f)

            entry.discord_message_id = message.id
            entry.discord_message_attachment_id = message.attachments[0].id if expected_f else None
            await entry.asave()

    async def format_entry(self, entry):
        content = ""
        f = None

        if entry.entry_type == Entry.EntryType.TEXT:
            content = entry.text_content
        elif entry.entry_type == Entry.EntryType.VIDEO:
            content = entry.video_url
        elif entry.entry_type in [Entry.EntryType.IMAGE, Entry.EntryType.FILE]:
            field = entry.image_file if entry.entry_type == Entry.EntryType.IMAGE else entry.file_upload
            if field:
                try:
                    if os.path.exists(field.path):
                        f = discord.File(field.path, filename=field.name.split('/')[-1])
                    else:
                        content = f"**Error:** File not found at {field.name}"
                except NotImplementedError:  # CDN
                    content = f"**Attachment:** {field.url}"
            else:
                content = "*No file attached*"
        else:
            self.logger.warning("Unhandled entry type: %s (entry id=%s)", entry.entry_type, entry.pk)
            content = f"*Unsupported content type: {entry.entry_type}*"

        return content, f

    async def update_live_message(self, post_id, channel_id, message_id):
        try:
            post = await Post.objects.aget(id=post_id)
        except Post.DoesNotExist:
            return

        try:
            channel = self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            self.logger.warning(f"Live message {message_id} not found for post {post_id}")
            return
        except (discord.Forbidden, discord.HTTPException):
            self.logger.exception(f"Error fetching live message {message_id}:")
            return

        first_entry = await post.entries.filter(entry_type='TEXT').order_by('order').afirst()

        content = f"**{post.title}**\n\n"
        if first_entry and first_entry.text_content:
            body = first_entry.text_content
            max_body = 1900 - len(content)
            if len(body) > max_body:
                body = body[:max_body] + "\n*…truncated*"
            content += body
        else:
            content += "*No content.*"

        try:
            await message.edit(content=content)
            self.logger.info(f"Updated live message {message_id} for post {post_id}")
        except discord.Forbidden:
            self.logger.error(f"Missing permissions to edit live message {message_id}")
        except discord.HTTPException:
            self.logger.exception(f"HTTPException editing live message {message_id}:")

    # Commands:
    async def tag_autocomplete(self, interaction: discord.Interaction, current: str):
        tags = [e async for e in Tag.objects.filter(name__icontains=current).order_by('order')[:25]]

        return [app_commands.Choice(name=t.name, value=str(t.id)) for t in tags]

    def _visible_posts(self, interaction: discord.Interaction):
        """Base queryset filtered by visibility permissions."""
        from app.discordwebcms.models import Post as PostModel
        from asgiref.sync import sync_to_async
        from django.contrib.auth import get_user_model
        return PostModel.objects.filter(visibility=PostModel.Visibility.PUBLIC)

    async def _can_view_staff(self, interaction: discord.Interaction) -> bool:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = await User.objects.filter(discorduser__discorduid=interaction.user.id).afirst()
        if not user:
            return False
        from asgiref.sync import sync_to_async
        return await sync_to_async(user.has_perm)("discordwebcms.view_staff_post")

    kb_group = app_commands.Group(name="kb", description="Knowledge base management commands")

    @kb_group.command(name="link", description="Link a KB article to a live Discord message")
    @requires_django_perm("discordwebcms.edit_post")
    async def kb_link(
        self,
        interaction: discord.Interaction,
        article_id: int,
        channel_id: str,
        message_id: str,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            post = await Post.objects.aget(id=article_id)
        except Post.DoesNotExist:
            await interaction.followup.send(f"No article found with ID {article_id}.", ephemeral=True)
            return

        try:
            ch_id = int(channel_id)
            msg_id = int(message_id)
        except ValueError:
            await interaction.followup.send("channel_id and message_id must be numeric snowflakes.", ephemeral=True)
            return

        # Verify the message exists
        try:
            channel = self.client.get_channel(ch_id) or await self.client.fetch_channel(ch_id)
            await channel.fetch_message(msg_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"Could not access that message: {e}", ephemeral=True)
            return

        post._skip_signals = True
        post._skip_version = True
        post.discord_channel_id = ch_id
        post.discord_message_id = msg_id
        await post.asave(update_fields=['discord_channel_id', 'discord_message_id'])

        # Trigger live update immediately
        await self.update_live_message(post.id, ch_id, msg_id)

        await interaction.followup.send(
            f"Article **{post.title}** linked to message `{msg_id}` in <#{ch_id}>. Content pushed.",
            ephemeral=True,
        )

    @app_commands.command(name="kbtopics")
    @app_commands.autocomplete(tag=tag_autocomplete)
    async def tag_info(self, interaction: discord.Interaction, tag: str):
        forum_channel = self.client.get_channel(self.channel_id)
        if not forum_channel:
            await interaction.response.send_message("No Active Forum.", ephemeral=True)
            return

        can_staff = await self._can_view_staff(interaction)
        t = await Tag.objects.aget(id=tag)

        qs = t.posts.all()
        if not can_staff:
            qs = qs.filter(visibility=Post.Visibility.PUBLIC)

        count = await qs.acount()
        reply = f"""# [{count}] Current Relevant KB "{t.name}" Entries:\n"""

        async for post in qs.order_by('order'):
            try:
                thread = forum_channel.get_thread(post.discord_thread_id) or await forum_channel.guild.fetch_channel(post.discord_thread_id)
                staff_flag = " 🔒" if post.visibility == Post.Visibility.STAFF else ""
                reply += f"{thread.mention}{staff_flag}\n"
            except discord.NotFound:
                reply += f"WIP: {post.title}\n"

        await interaction.response.send_message(reply, ephemeral=True)

    @app_commands.command(name="kbsearch", description="Search the knowledge base by title or content")
    async def kb_search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)

        forum_channel = self.client.get_channel(self.channel_id)
        if not forum_channel:
            await interaction.followup.send("No Active Forum.", ephemeral=True)
            return

        can_staff = await self._can_view_staff(interaction)

        qs = Post.objects.filter(title__icontains=query)
        if not can_staff:
            qs = qs.filter(visibility=Post.Visibility.PUBLIC)

        # Also match on text entry content
        text_match_qs = Post.objects.filter(entries__text_content__icontains=query)
        if not can_staff:
            text_match_qs = text_match_qs.filter(visibility=Post.Visibility.PUBLIC)

        from django.db.models import Q as DQ
        combined_qs = Post.objects.filter(
            DQ(title__icontains=query) | DQ(entries__text_content__icontains=query)
        ).distinct()
        if not can_staff:
            combined_qs = combined_qs.filter(visibility=Post.Visibility.PUBLIC)

        results = [p async for p in combined_qs.order_by('order')[:20]]

        if not results:
            await interaction.followup.send(f"No KB entries found matching **{query[:100]}**.", ephemeral=True)
            return

        lines = [f"# KB Search: `{query[:80]}`\n"]
        for post in results:
            if post.discord_thread_id:
                try:
                    thread = forum_channel.get_thread(post.discord_thread_id) or await forum_channel.guild.fetch_channel(post.discord_thread_id)
                    staff_flag = " 🔒" if post.visibility == Post.Visibility.STAFF else ""
                    lines.append(f"{thread.mention}{staff_flag}")
                except discord.NotFound:
                    lines.append(f"• {post.title} *(thread missing)*")
            else:
                lines.append(f"• {post.title} *(not yet synced)*")

        reply = "\n".join(lines)
        # Discord message cap — truncate gracefully
        if len(reply) > 1900:
            reply = reply[:1900] + "\n*…truncated*"

        await interaction.followup.send(reply, ephemeral=True)


async def setup(client):
    cog = CMSSyncCog(client)
    await client.add_cog(cog)
