import discord
from discord.ext import commands, tasks
from discord.ui import LayoutView, Container, Section, TextDisplay, Thumbnail, Button, Separator
from asgiref.sync import sync_to_async
from contextlib import suppress
import logging

from django.conf import settings

from app.candidacy.models import AssignableDiscipline
from app.discordauth.models import DiscordUser
from app.preferences.utils import aget_global_preference
from app.main.util.formatting import SafeDict
from ..preferences import DisciplineSelectionChannelID, DisciplinePortalText, DisciplineLogChannelID



def _component_has_id(component, target_id: str) -> bool:
    """Recursively search a discord component tree for a given custom_id."""
    if getattr(component, 'custom_id', None) == target_id:
        return True
    for child in getattr(component, 'children', []):
        if _component_has_id(child, target_id):
            return True
    accessory = getattr(component, 'accessory', None)
    if accessory and _component_has_id(accessory, target_id):
        return True
    return False


class DisciplineSelect(discord.ui.Select):
    def __init__(self, disciplines: list[AssignableDiscipline], parent_view):
        self.parent_view = parent_view
        self.disciplines_map = {str(d.id): d for d in disciplines}
        options = [
            discord.SelectOption(
                label=d.name[:100],
                description=(d.description[:97] + "...") if len(d.description) > 97 else d.description or None,
                value=str(d.id)
            )
            for d in disciplines
        ]
        super().__init__(placeholder="Choose an interest...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        discipline = self.disciplines_map[self.values[0]]

        try:
            du = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
            user = du.user
            if not user:
                raise DiscordUser.DoesNotExist
        except DiscordUser.DoesNotExist:
            await interaction.response.send_message("❌ Your Discord account must be linked to join an interest.", ephemeral=True)
            return

        # Re-fetch groups async — prefetch cache is gone after bot restarts
        target_groups = [g async for g in discipline.permission_groups.all()]
        if not target_groups:
            await interaction.response.send_message("❌ This interest has no assigned permission groups. Please contact staff.", ephemeral=True)
            return

        # Check if already a member of all groups associated with this discipline
        group_ids = [g.id for g in target_groups]
        user_has_count = await user.groups.filter(id__in=group_ids).acount()
        if user_has_count == len(group_ids):
            await interaction.response.send_message(f"ℹ️ You are already a member of the **{discipline.name}** interest.", ephemeral=True)
            return

        # Add groups
        await user.groups.aadd(*target_groups)

        # Success response
        await interaction.response.send_message(f"✅ You have joined the **{discipline.name}** interest!", ephemeral=True)

        # Discipline assignment log
        log_chan_id = await aget_global_preference(DisciplineLogChannelID.import_path)
        if log_chan_id:
            log_channel = interaction.client.get_channel(log_chan_id)
            if log_channel:
                embed = discord.Embed(
                    title="Discipline Assigned",
                    description=f"{interaction.user.mention} has joined **{discipline.name}**.",
                    color=discord.Color.green(),
                )
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                embed.timestamp = discord.utils.utcnow()
                await log_channel.send(embed=embed)

        # Notification logic
        if discipline.notification_channel_id:
            channel = interaction.client.get_channel(discipline.notification_channel_id)
            if not channel:
                with suppress(discord.NotFound, discord.Forbidden):
                    channel = await self.parent_view.parent_cog.client.fetch_channel(discipline.notification_channel_id)
            if channel:
                msg = discipline.notification_message.format_map(SafeDict(
                    user=interaction.user.mention,
                    name=interaction.user.display_name,
                    discipline=discipline.name
                ))
                try:
                    await channel.send(msg)
                except discord.Forbidden:
                    self.parent_view.parent_cog.logger.warning(f"Bot lacks permission to send notification in channel {discipline.notification_channel_id}")


class DisciplineEntryView(LayoutView):
    def __init__(self, portal_text: str, parent_cog):
        super().__init__(timeout=None)
        self.parent_cog = parent_cog
        # Main Wrapper
        container = Container()

        # Header Section with Organization Icon
        icon_url = "https://www.gstatic.com/android/keyboard/emojikitchen/20201001/u1f47f/u1f47f_u1f480.png"
        header_section = Section(accessory=Thumbnail(media=icon_url))
        header_section.add_item(TextDisplay(portal_text))
        container.add_item(header_section)

        container.add_item(Separator(visible=True, spacing=discord.SeparatorSpacing.small))

        # Action Section with the Entry Button
        start_btn = Button(
            label="⚔️ Choose Discipline",
            style=discord.ButtonStyle.primary,
            custom_id="candidacy_discipline_selection_start_btn"
        )
        start_btn.callback = self.start_selection

        action_section = Section(accessory=start_btn)
        action_section.add_item(TextDisplay("### Eligibility & Selection\nClick the button to view the paths you are currently eligible to join."))
        container.add_item(action_section)

        self.add_item(container)

    async def start_selection(self, interaction: discord.Interaction):
        # Resolve User first to filter list
        try:
            du = await DiscordUser.objects.select_related("user").aget(discorduid=interaction.user.id)
            user = du.user
            if not user:
                raise DiscordUser.DoesNotExist
        except DiscordUser.DoesNotExist:
            await interaction.response.send_message("❌ Your Discord account must be linked to view interests.", ephemeral=True)
            return

        # Fetch and filter by permission
        available_disciplines = []
        async for d in AssignableDiscipline.objects.filter(is_active=True).prefetch_related("permission_groups"):
            if await user.ahas_perm("candidacy.can_self_assign_discipline", d):
                available_disciplines.append(d)

        if not available_disciplines:
            await interaction.response.send_message("🛡️ You are not currently eligible to join any interests.", ephemeral=True)
            return

        view = discord.ui.View(timeout=900)
        view.add_item(DisciplineSelect(available_disciplines, parent_view=self))
        await interaction.response.send_message("Please select a interest from the list below:", view=view, ephemeral=True)


class DisciplineSelectionCog(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        self.logger = logging.getLogger(__name__)

        self.client.add_view(DisciplineEntryView(portal_text="", parent_cog=self))
        self.startup_task.start()

    def cog_unload(self):
        self.startup_task.cancel()

    @tasks.loop(count=1)
    async def startup_task(self):
        await self.initialize_portal()

    @startup_task.before_loop
    async def before_startup(self):
        await self.client.wait_until_ready()

    async def initialize_portal(self):
        chan_id = await aget_global_preference(DisciplineSelectionChannelID.import_path)
        if not chan_id:
            self.logger.warning("DisciplineSelectionChannelID is not configured.")
            return

        channel = self.client.get_channel(chan_id) or await self.client.fetch_channel(chan_id)
        if not channel:
            self.logger.error(f"Discipline selection channel {chan_id} not found.")
            return

        # Bulk-delete old portal messages before re-posting.
        # LayoutView nests the button inside Container → Section.accessory, so we
        # must recurse the full component tree rather than checking one level down.
        # channel.purge() groups deletes into batches of 100 (much faster, avoids
        # per-message rate limits). Messages >14 days old are silently skipped.
        def _is_portal(m: discord.Message) -> bool:
            return m.author == self.client.user and any(
                _component_has_id(c, "candidacy_discipline_selection_start_btn")
                for c in m.components
            )

        with suppress(discord.HTTPException):
            await channel.purge(limit=50, check=_is_portal, bulk=True)

        portal_text = await aget_global_preference(DisciplinePortalText.import_path)
        if not portal_text:
            self.logger.warning("DisciplinePortalText is not configured.")

        await channel.send(view=DisciplineEntryView(portal_text=portal_text, parent_cog=self))
        self.logger.info(f"Discipline selection portal posted in channel {chan_id}")


async def setup(client: commands.Bot):
    await client.add_cog(DisciplineSelectionCog(client))
