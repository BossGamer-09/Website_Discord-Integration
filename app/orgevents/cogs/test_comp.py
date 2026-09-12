import discord
from discord.ext import commands, tasks
from discord.ui import LayoutView, Container, Section, TextDisplay, Thumbnail, MediaGallery, ActionRow, Button, UserSelect, Separator
import aiohttp
import io
import logging
from contextlib import suppress

from app.preferences.utils import aget_global_preference
from app.orgevents.preferences import OrganicEventsMainChannelID


def _component_has_id(component, target_id: str) -> bool:
    if getattr(component, 'custom_id', None) == target_id:
        return True
    for child in getattr(component, 'children', []):
        if _component_has_id(child, target_id):
            return True
    accessory = getattr(component, 'accessory', None)
    if accessory and _component_has_id(accessory, target_id):
        return True
    return False




class ActionControlView2(LayoutView):
    def __init__(self):
        # A timeout of None keeps it persistent
        super().__init__(timeout=None)

        # 1. The Main Wrapper
        container = Container()

        icon_url = "https://www.gstatic.com/android/keyboard/emojikitchen/20201001/u1f47f/u1f47f_u1f480.png"
        accessory = Thumbnail(media=icon_url)
        audit_section = Section(accessory=accessory)
        audit_section.add_item(TextDisplay(
            "# **__`Organic Action Center`__**\n"
            "Press a button below to report a **time-critical organic event.**\n"
            "This will create a dedicated thread and notify the appropriate teams."
        ))
        container.add_item(audit_section)
        self.add_item(container)


        sep = Separator(visible=True, spacing=discord.SeparatorSpacing.small)
        self.add_item(sep)


        container = Container()

        gallery = MediaGallery()
        gallery.add_item(media="https://dummyimage.com/800x200/5065F2/ffffff.png&text=💀DEATHWATCH")
        container.add_item(gallery)

        deathwatch_btn = Button(label="REQUEST 💀", style=discord.ButtonStyle.primary, custom_id="organic_action_DEATHWATCH")
        deathwatch_btn.callback = self.deathwatch_callback
        top_section = Section(accessory=deathwatch_btn)
        top_section.add_item(TextDisplay(
            f"### Expected to be requested when __intending to fight__ and one of;\n"
            f"- You lose at least 25% of your force or 3 individuals whichever is higher\n"
            f"- Expect imminent high skill PVP\n"
            f"- Have vulnerable assets in an operation and combat has occurred\n"
            f"- Are outnumbered in combat and your goal requires engagement\n"
        ))
        container.add_item(top_section)
        self.add_item(container)


        sep = Separator(visible=True, spacing=discord.SeparatorSpacing.large)
        self.add_item(sep)


        container = Container()

        gallery = MediaGallery()
        gallery.add_item(media="https://dummyimage.com/800x200/5860F2/ffffff.png&text=⚔️PvP%20Alert")
        container.add_item(gallery)

        pvp_btn = Button(label="REQUEST ⚔️", style=discord.ButtonStyle.primary, custom_id="organic_action_SCPVP")
        pvp_btn.callback = self.pvp_callback
        top_section = Section(accessory=pvp_btn)
        top_section.add_item(TextDisplay(
            f"### Expected to be used when there is a PvP opportunity;\n"
            f"- Use for __lower stakes than Deathwatch__ or when looking for content\n"
        ))
        container.add_item(top_section)
        self.add_item(container)


        sep = Separator(visible=True, spacing=discord.SeparatorSpacing.large)
        self.add_item(sep)


        container = Container()

        gallery = MediaGallery()
        gallery.add_item(media="https://dummyimage.com/800x200/586502/ffffff.png&text=💰LOOT%20GOBLIN")
        container.add_item(gallery)

        loot_btn = Button(label="REQUEST 💰", style=discord.ButtonStyle.primary, custom_id="organic_action_LOOTGOBLIN")
        loot_btn.callback = self.loot_callback
        top_section = Section(accessory=loot_btn)
        top_section.add_item(TextDisplay(
            f"### Expected to be requested when __need loot pickup__ under these conditions;\n"
            f"- Active PvP combat\n"
            f"- Assets at risk\n"
        ))
        container.add_item(top_section)
        self.add_item(container)


    # --- Callbacks ---
    # LayoutView doesn't support discord.py's persistent-view re-registration.
    # Callbacks are wired here for new messages; on restart, OrganicActionsCog
    # registers ActionControlView (same custom_ids) to handle old messages.
    async def _dispatch_event(self, interaction: discord.Interaction, event_type: str):
        cog = interaction.client.cogs.get('OrganicActionsCog')
        if not cog or not cog.parent_chan:
            await interaction.response.send_message("⚠️ Event system unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await cog.start_event(interaction.user, event_type)
            await interaction.followup.send(f"{event_type} event initiated.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Failed to start event: {e}", ephemeral=True)

    async def deathwatch_callback(self, interaction: discord.Interaction):
        await self._dispatch_event(interaction, "DEATHWATCH")

    async def pvp_callback(self, interaction: discord.Interaction):
        await self._dispatch_event(interaction, "SCPVP")

    async def loot_callback(self, interaction: discord.Interaction):
        await self._dispatch_event(interaction, "LOOTGOBLIN")




class GuildConfigView(LayoutView):
    def __init__(self, owner_id: int, member_count: int, banner_file: discord.File):
        # A timeout of None keeps it persistent
        super().__init__(timeout=None)

        # 1. The Main Wrapper
        container = Container()

        # 2. Top Section: Text + Inline Leaf Thumbnail

        icon_url = "https://dummyimage.com/128x128/5865F2/ffffff.png&text=Icon"
        accessory = Thumbnail(media=icon_url, description="describing the icon")

        top_section = Section(accessory=accessory)
        top_section.add_item(TextDisplay(
            f"## Configure Guild Development\n"
            f"### title Dev - Discord\n"
            f"• Owner: <@{owner_id}>\n"
            f"• Total members: {member_count}"
        ))

        container.add_item(top_section)

        gallery = MediaGallery()
        banner_url = "https://dummyimage.com/600x200/5865F2/ffffff.png&text=Banner"
        gallery.add_item(media=banner_url)
        container.add_item(gallery)

        # Audit Log Section: Text + Inline Button
        access_btn = Button(label="Access", style=discord.ButtonStyle.primary, custom_id="btn_access")
        access_btn.callback = self.access_callback

        audit_section = Section(accessory=access_btn)
        audit_section.add_item(TextDisplay("### Audit Logs\nView the server's audit logs"))

        container.add_item(audit_section)

        # Dropdown Menu
        dropdown_row = ActionRow()
        member_select = UserSelect(placeholder="Select a member", custom_id="select_member")
        member_select.callback = self.member_callback
        dropdown_row.add_item(member_select)
        
        container.add_item(dropdown_row)

        # Add the entire constructed container to the LayoutView
        self.add_item(container)

    # --- Callbacks ---
    async def access_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("Accessing audit logs...", ephemeral=True)

    async def member_callback(self, interaction: discord.Interaction):
        # UserSelect automatically handles fetching the server member
        selected_user = interaction.data.get('values', [None])[0]
        await interaction.response.send_message(f"Selected member: <@{selected_user}>", ephemeral=True)


class DashboardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        # ActionControlView2 (LayoutView) can't be re-registered as persistent.
        # OrganicActionsCog registers ActionControlView (same custom_ids) for old messages.
        self.startup_task.start()

    def cog_unload(self):
        self.startup_task.cancel()

    @tasks.loop(count=1)
    async def startup_task(self):
        await self._post_panel()

    @startup_task.before_loop
    async def before_startup(self):
        await self.bot.wait_until_ready()

    async def _post_panel(self):
        chan_id = await aget_global_preference(OrganicEventsMainChannelID.import_path)
        if not chan_id:
            self.logger.warning("OrganicEventsMainChannelID is not configured — skipping panel post.")
            return

        channel = self.bot.get_channel(int(chan_id)) or await self.bot.fetch_channel(int(chan_id))
        if not channel:
            self.logger.error(f"Organic events channel {chan_id} not found.")
            return

        def _is_panel(m: discord.Message) -> bool:
            return m.author == self.bot.user and any(
                _component_has_id(c, "organic_action_DEATHWATCH")
                for c in m.components
            )

        with suppress(discord.HTTPException):
            await channel.purge(limit=50, check=_is_panel, bulk=True)

        await channel.send(view=ActionControlView2())
        self.logger.info(f"Organic Action Center panel posted in channel {chan_id}")


async def setup(bot):
    await bot.add_cog(DashboardCog(bot))
