import discord
from discord.ext import commands, tasks
from asgiref.sync import sync_to_async
import logging
import asyncio
import json

from django.utils.timezone import now
from django.contrib.auth.models import Group
from django.conf import settings
from django.contrib.auth import get_user_model

import redis.asyncio as redis

from app.candidacy.models import ApplicationType, ApplicationRecord
from app.discordauth.models import DiscordUser
from app.preferences.utils import aget_global_preference
from app.main.util.discord_command_checks import _verify_and_cache_generic_permission, _verify_and_cache_permission, MissingDjangoObjectPermission, MissingDjangoPermission, AccountNotLinked
from app.main.util.db import get_users_with_permission_on
from ..preferences import StaffApplicationsChanID, TrainingApplicationsChanID



async def create_application(interaction: discord.Interaction, app_type: ApplicationType, answers: list[str]):
    try:
        discord_user = await DiscordUser.objects.select_related('user').aget(discorduid=interaction.user.id)
        applicant = discord_user.user
    except DiscordUser.DoesNotExist:
        if not interaction.response.is_done():
            await interaction.response.send_message("You must link your Discord account to do this.", ephemeral=True)
        else:
            await interaction.followup.send("You must link your Discord account to do this.", ephemeral=True)
        return

    # Create Thread
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        channel = channel.parent

    thread_name = f"{interaction.user.display_name} - {app_type.name}"[:100]
    if isinstance(channel, discord.ForumChannel):
        thread_with_message = await channel.create_thread(name=thread_name, content=f"Application by {interaction.user.mention}")
        thread = thread_with_message.thread
    else:
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=False
        )
        await thread.add_user(interaction.user)

    # Save to DB
    app_record = await ApplicationRecord.objects.acreate(
        applicant=applicant,
        app_type=app_type,
        status=ApplicationRecord.Status.PENDING,
        thread_id=thread.id,
        answer_1=answers[0],
        answer_2=answers[1],
        answer_3=answers[2],
        answer_4=answers[3],
        answer_5=answers[4]
    )

    @sync_to_async
    def aget_all_reviewers_discord_ids():
        users = (get_users_with_permission_on("candidacy.can_review_application", app_record) | get_users_with_permission_on("candidacy.can_review_application_type", app_type)).distinct().prefetch_related("discorduser")
        discord_ids = users.exclude(discorduser__isnull=True).values_list('discorduser__discorduid', flat=True)
        return discord_ids

    reviewers_discord_ids = await aget_all_reviewers_discord_ids()
    for discord_id in reviewers_discord_ids:
        await thread.add_user(discord.Object(id=discord_id))
        await asyncio.sleep(0.1)

    view = ApplicationReviewView(application_id=app_record.id)

    # Post details in thread
    embed = discord.Embed(
        title=f"{app_type.get_kind_display()} Application: {app_type.name}",
        color=discord.Color.blue()
    )
    embed.add_field(name="Applicant", value=interaction.user.mention, inline=False)

    has_question = False
    for i in range(1, 6):
        q_text = getattr(app_type, f"question_{i}")
        if q_text and answers[i-1]:
            embed.add_field(name=q_text, value=answers[i-1], inline=False)
            has_question = True

    if not has_question:
        embed.description = "No questions were required for this application."

    await thread.send(embed=embed, view=view)

    msg = f"Application submitted! Check {thread.mention}"
    if not interaction.response.is_done():
        await interaction.response.send_message(msg, ephemeral=True)
    else:
        await interaction.followup.send(msg, ephemeral=True)


class ApplicationModal(discord.ui.Modal):
    def __init__(self, application_type: ApplicationType):
        title = f"Apply for {application_type.name}"[:45]
        super().__init__(title=title)
        self.application_type = application_type

        self.questions = []
        for i in range(1, 6):
            q_text = getattr(application_type, f"question_{i}")
            if q_text:
                required = True
                text_input = discord.ui.TextInput(
                    label=q_text[:45],
                    style=discord.TextStyle.paragraph,
                    required=required,
                    max_length=1000
                )
                self.add_item(text_input)
                self.questions.append(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        answers = [q.value for q in self.questions]
        while len(answers) < 5:
            answers.append("")

        await create_application(interaction, self.application_type, answers)


class PositionSelect(discord.ui.Select):
    def __init__(self, items):
        self.items_map = {str(item.id): item for item in items}

        options = []
        for item in items:
            options.append(discord.SelectOption(
                label=item.name[:100],
                description=(item.description[:97] + "...") if item.description else None,
                value=str(item.id)
            ))

        super().__init__(placeholder="Select an option to apply for...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        selected_item = self.items_map[selected_id]

        try:
            try:
                await _verify_and_cache_generic_permission(interaction.user.id, "candidacy.can_submit_application")
            except MissingDjangoPermission:
                await _verify_and_cache_permission(interaction.user.id, "candidacy.can_submit_application_type", selected_item)

        except (MissingDjangoObjectPermission, AccountNotLinked) as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        # If no questions are defined, skip modal and create directly
        if not any(getattr(selected_item, f"question_{i}") for i in range(1, 6)):
            await interaction.response.defer(thinking=True, ephemeral=True)
            await create_application(interaction, selected_item, ["", "", "", "", ""])
        else:
            await interaction.response.send_modal(ApplicationModal(selected_item))


class SelectionView(discord.ui.View):
    def __init__(self, items):
        super().__init__(timeout=None)
        self.add_item(PositionSelect(items))


class StaffEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply for Staff", style=discord.ButtonStyle.primary, custom_id="candidacy_persistent_staff_apply_btn")
    async def staff_apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = await ApplicationType.objects.filter(kind=ApplicationType.Kind.STAFF, is_active=True)[:25].alist()
        if not items:
            await interaction.response.send_message("There are currently no open staff positions.", ephemeral=True)
            return
        await interaction.response.send_message("Please select the staff position you are applying for:", view=SelectionView(items), ephemeral=True)


class TrainingEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply for Training", style=discord.ButtonStyle.success, custom_id="candidacy_persistent_training_apply_btn")
    async def training_apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = await ApplicationType.objects.filter(kind=ApplicationType.Kind.TRAINING, is_active=True)[:25].alist()
        if not items:
            await interaction.response.send_message("There are currently no active trainings.", ephemeral=True)
            return
        await interaction.response.send_message("Please select the training you want to apply for:", view=SelectionView(items), ephemeral=True)


class ApplicationCustomEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply for Staff", style=discord.ButtonStyle.primary, custom_id="candidacy_persistent_staff_apply_btn")
    async def staff_apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = await ApplicationType.objects.filter(kind=ApplicationType.Kind.STAFF, is_active=True)[:25].alist()
        if not items:
            await interaction.response.send_message("There are currently no open staff positions.", ephemeral=True)
            return
        await interaction.response.send_message("Please select the staff position you are applying for:", view=SelectionView(items), ephemeral=True)

    @discord.ui.button(label="Apply for Training", style=discord.ButtonStyle.success, custom_id="candidacy_persistent_training_apply_btn")
    async def training_apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = await ApplicationType.objects.filter(kind=ApplicationType.Kind.TRAINING, is_active=True)[:25].alist()
        if not items:
            await interaction.response.send_message("There are currently no active trainings.", ephemeral=True)
            return
        await interaction.response.send_message("Please select the training you want to apply for:", view=SelectionView(items), ephemeral=True)


class ApplicationReviewView(discord.ui.View):
    def __init__(self, application_id: int):
        super().__init__(timeout=None)
        self.application_id = application_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="candidacy_app_accept_btn")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_review(interaction, ApplicationRecord.Status.APPROVED)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="candidacy_app_deny_btn")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_review(interaction, ApplicationRecord.Status.DENIED)

    async def _handle_review(self, interaction: discord.Interaction, status):
        await interaction.response.defer(thinking=True, ephemeral=True)

        app_record = await ApplicationRecord.objects.select_related('app_type').prefetch_related('applicant__discorduser').prefetch_related('app_type__on_accept_gives_permission_groups').aget(id=self.application_id)

        try:
            try:
                await _verify_and_cache_permission(interaction.user.id, "candidacy.can_review_application", app_record)
            except MissingDjangoObjectPermission:
                 await _verify_and_cache_permission(interaction.user.id, "candidacy.can_review_application_type", app_record.app_type)

        except (MissingDjangoObjectPermission, AccountNotLinked) as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        try:
            discord_user = await DiscordUser.objects.select_related('user').aget(discorduid=interaction.user.id)
            reviewer = discord_user.user
        except DiscordUser.DoesNotExist:
            await interaction.followup.send("Your discord account must be linked to do this.", ephemeral=True)
            raise

        app_record.status = status
        app_record.reviewer = reviewer
        app_record.reviewed_at = now()
        await app_record.asave()

        # Update UI
        for child in self.children:
            child.disabled = True

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green() if status == ApplicationRecord.Status.APPROVED else discord.Color.red()
        embed.add_field(name="Status", value=ApplicationRecord.Status.APPROVED.label if status == ApplicationRecord.Status.APPROVED else ApplicationRecord.Status.DENIED.label)
        embed.add_field(name="Reviewed By", value=interaction.user.mention)

        await interaction.message.edit(embed=embed, view=self)

        if status == ApplicationRecord.Status.APPROVED:
            msg = f"# Application {ApplicationRecord.Status.APPROVED.label}.\n> 🎉 Congratulations! <@{app_record.applicant.discorduser.pk}>\n"

            if app_record.app_type.on_accept_disclaimer:
                msg += f"{app_record.app_type.on_accept_disclaimer}\n"

            gives_permission_groups = await app_record.app_type.on_accept_gives_permission_groups.alist()

            if gives_permission_groups:
                from app.org.models import DiscordRole
                discord_roles = await DiscordRole.objects.filter(permission_groups__in=(pg.pk for pg in gives_permission_groups)).distinct().alist()

                await app_record.applicant.groups.aadd(*gives_permission_groups)

                msg +=  f"-# You will be automatically given Permission Groups: {', '.join([x.name for x in gives_permission_groups])}. Which imply roles: {''.join(['<@&{}>'.format(x.role_id) for x in discord_roles])}"

        elif status == ApplicationRecord.Status.DENIED:
            msg = f"# Application {ApplicationRecord.Status.DENIED.label}."

        else:
            await interaction.followup.send(f"Unhandled application status: `{status}`. No action taken.", ephemeral=True)
            return

        await interaction.channel.send(msg, allowed_mentions=discord.AllowedMentions(roles=False))
        await interaction.channel.channel.edit(default_auto_archive_duration=1440)        

        await interaction.followup.send(f"Done. {status}. Thread will autoarchive in 24h", ephemeral=True)      


class CandidacyAppsCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.client.add_view(ApplicationCustomEntryView())
        self.client.add_view(StaffEntryView())
        self.client.add_view(TrainingEntryView())
        self.logger = logging.getLogger(__name__)
        self.User = get_user_model()
        self.startup_task.start()

    async def cog_unload(self):
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
        await self.initialize_views()
        await self.full_sync()

    @startup_task.before_loop
    async def before_startup_task(self):
        await self.client.wait_until_ready()

    async def full_sync(self):
        self.logger.info("Starting full sync of application threads...")

        @sync_to_async
        def get_sync_data():
            # Only sync pending applications; approved/denied threads are closed
            pending_apps = list(ApplicationRecord.objects.filter(
                status=ApplicationRecord.Status.PENDING
            ).select_related('app_type', 'applicant__discorduser'))

            sync_data = []
            for app in pending_apps:
                # Calculate expected reviewers for this specific application
                users = (get_users_with_permission_on("candidacy.can_review_application", app) | 
                         get_users_with_permission_on("candidacy.can_review_application_type", app.app_type)).distinct().select_related("discorduser")

                reviewer_discord_ids = set(users.exclude(discorduser__isnull=True).values_list('discorduser__discorduid', flat=True))

                applicant_discord_id = None
                if hasattr(app.applicant, 'discorduser'):
                    applicant_discord_id = app.applicant.discorduser.discorduid

                sync_data.append({
                    'thread_id': app.thread_id,
                    'reviewer_discord_ids': reviewer_discord_ids,
                    'applicant_discord_id': applicant_discord_id
                })
            return sync_data

        # 1. Fetch all required data from DB safely
        sync_data = await get_sync_data()

        # 2. Process Discord API changes
        for data in sync_data:
            try:
                thread = await self.get_thread_by_id(data['thread_id'])
            except Exception:
                continue

            expected_members_ids = data['reviewer_discord_ids'] + {data['applicant_discord_id']}
            current_member_ids = {tm.id for tm in thread.members}

            to_add = expected_members_ids - current_member_ids
            to_remove = current_member_ids - expected_members_ids
            to_remove.discard(self.client.user.id)

            for user_id in to_add:
                if member := await self.get_member_by_id(user_id):
                    try:
                        await self.add_user_to_thread(member, thread)
                        await asyncio.sleep(0.1)  # Prevent Discord API rate limits
                        self.logger.info(f"Full Sync: Added {member} to {thread.name}")
                    except Exception as e:
                        self.logger.error(f"Full Sync: Failed to add missing reviewer {user_id} to thread {thread.name}: {e}")

            for user_id in to_remove:
                if member := await self.get_member_by_id(user_id):
                    try:
                        await self.remove_user_from_thread(member, thread)
                        await asyncio.sleep(0.1)  # Prevent Discord API rate limits
                        self.logger.info(f"Full Sync: Removed {member} from {thread.name}")
                    except Exception as e:
                        self.logger.error(f"Full Sync: Failed to add missing user {user_id} to thread {thread.name}: {e}")

        self.logger.info("Completed full sync of application threads.")

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
            self.logger.exception("CandidacyAppsCog Sync Error:")

    async def process_event(self, data):
        action = data.get('action')
        obj_type = data.get('type')

        if obj_type != "User.Permission":
            return

        if data.get('permission') in ["candidacy.can_review_application", "candidacy.can_review_application_type"]:
            user_id = data.get('user_id')

            try:
                user = await self.User.objects.prefetch_related("discorduser").aget(pk=user_id)
                discord_id = user.discorduser.discorduid
            except (self.User.DoesNotExist, AttributeError):
                self.logger.warning(f"User {user_id} not found or not linked to Discord.")
                return

            member = await self.get_member_by_id(discord_id)
            if not member:
                self.logger.warning(f"Member {discord_id} not found in Discord.")
                return

            if action == "added":
                base_qs = await ApplicationRecord.ainstances_with_perm_for("candidacy.can_review_application", user, pk=False)

                async for app in base_qs:
                    try:
                        thread = await self.get_thread_by_id(app.thread_id)
                    except Exception:
                        continue

                    await self.add_user_to_thread(member, thread)

            elif action == "removed":
                allowed_ids = await ApplicationRecord.ainstances_with_perm_for("candidacy.can_review_application", user, pk=True)

                async for app in ApplicationRecord.objects.exclude(pk__in=allowed_ids):
                    try:
                        thread = await self.get_thread_by_id(app.thread_id)
                    except Exception:
                        continue

                    await self.remove_user_from_thread(member, thread)

    async def initialize_views(self):
        self.staff_chan_id = await aget_global_preference(StaffApplicationsChanID.import_path)
        self.training_chan_id = await aget_global_preference(TrainingApplicationsChanID.import_path)

        channels_to_setup = {}
        if self.staff_chan_id:
            channels_to_setup[self.staff_chan_id] = channels_to_setup.get(self.staff_chan_id, []) + ['staff']
        if self.training_chan_id:
            channels_to_setup[self.training_chan_id] = channels_to_setup.get(self.training_chan_id, []) + ['training']

        for chan_id, types in channels_to_setup.items():
            channel = self.client.get_channel(chan_id)
            if not channel:
                self.logger.error(f"Application channel not found: {chan_id}")
                continue

            # Clear old bot messages
            async for message in channel.history(limit=20):
                if message.author == self.client.user:
                    try:
                        if message.embeds and ("Application" in message.embeds[0].title or "Training" in message.embeds[0].title):
                            await message.delete()
                            await asyncio.sleep(0.5)
                    except:
                        pass

            # Post new message based on types
            if 'staff' in types and 'training' in types:
                embed = discord.Embed(
                    title="BlightVeil Applications",
                    description="Click below to apply for Staff Positions or Training Programs.",
                    color=discord.Color.gold()
                )
                await channel.send(embed=embed, view=ApplicationCustomEntryView())
            elif 'staff' in types:
                embed = discord.Embed(
                    title="Staff Applications",
                    description="Click below to apply for an open Staff Position.",
                    color=discord.Color.gold()
                )
                await channel.send(embed=embed, view=StaffEntryView())
            elif 'training' in types:
                embed = discord.Embed(
                    title="Training Programs",
                    description="Click below to apply for an available Training Program.",
                    color=discord.Color.gold()
                )
                await channel.send(embed=embed, view=TrainingEntryView())

            self.logger.info(f"Completed setup application portal in channel {chan_id}")

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

    async def get_member_by_id(self, user_id):
        guild_id = await self.client.aget_main_guild_id()
        guild = self.client.get_guild(guild_id)
        if guild is None:
            self.logger.error(f"Main guild not found in cache (id={guild_id}), cannot fetch member.")
            return None
        member = None

        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except discord.NotFound:
            self.logger.error(f"User {user_id} is not in Guild ID {guild.id}, cannot fetch member.")
        except discord.Forbidden:
            self.logger.error(f"Bot missing permission to access members.")

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

async def setup(client):
    await client.add_cog(CandidacyAppsCog(client))
