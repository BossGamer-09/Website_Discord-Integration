import logging
import discord
from discord.ext import commands
from discord import app_commands
from contextlib import suppress

from app.preferences.utils import aget_global_preference
from app.main.util.discord_command_checks import requires_django_perm
from app.org.preferences import NicknameChangeLogChannelID, NicknameChangeReviewChannelID


def _parse_embed(interaction: discord.Interaction) -> tuple[int, str]:
    """Extract requester_id and desired_nickname from the review embed."""
    requester_id = 0
    desired_nickname = ""
    try:
        footer = interaction.message.embeds[0].footer.text  # "User ID: 123456"
        requester_id = int(footer.split("User ID: ")[1].strip())
    except Exception:
        pass
    try:
        fields = interaction.message.embeds[0].fields
        desired_nickname = next((f.value for f in fields if f.name == "Desired Nickname"), "")
    except Exception:
        pass
    return requester_id, desired_nickname


class NicknameRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="nickreq_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        requester_id, desired_nickname = _parse_embed(interaction)
        await _handle_approve(interaction, requester_id, desired_nickname)

    @discord.ui.button(label="Edit & Approve", style=discord.ButtonStyle.primary, custom_id="nickreq_edit")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        requester_id, desired_nickname = _parse_embed(interaction)
        await interaction.response.send_modal(
            NicknameEditModal(requester_id=requester_id, original_nickname=desired_nickname)
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="nickreq_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        requester_id, _ = _parse_embed(interaction)
        await _handle_deny(interaction, requester_id)


class NicknameEditModal(discord.ui.Modal, title="Edit Nickname"):
    new_nickname = discord.ui.TextInput(
        label="New Nickname",
        placeholder="Enter the approved nickname",
        required=True,
        max_length=32,
    )

    def __init__(self, requester_id: int, original_nickname: str):
        super().__init__()
        self.requester_id = requester_id
        self.original_nickname = original_nickname

    async def on_submit(self, interaction: discord.Interaction):
        # Modal submit needs its own response first, then we edit the original message
        await interaction.response.defer(ephemeral=True)

        member = interaction.guild.get_member(self.requester_id)
        if not member:
            with suppress(discord.NotFound):
                member = await interaction.guild.fetch_member(self.requester_id)
        if not member:
            await interaction.followup.send("❌ Member not found.", ephemeral=True)
            return

        old_nickname = member.display_name
        new_nickname = self.new_nickname.value
        try:
            await member.edit(nick=new_nickname)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Bot cannot change that member's nickname. They may have a higher role than the bot, or are the server owner.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Failed to change nickname: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="Nickname Approved",
            description=f"{member.mention}'s nickname has been changed.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Before", value=old_nickname, inline=True)
        embed.add_field(name="After", value=new_nickname, inline=True)
        embed.add_field(name="Approved By", value=interaction.user.mention, inline=False)
        embed.timestamp = discord.utils.utcnow()

        # Edit the original review message (the message the button was on)
        with suppress(discord.NotFound, discord.HTTPException):
            await interaction.message.edit(embed=embed, view=None)

        await _send_log(interaction.client, interaction.guild, member, old_nickname, new_nickname, interaction.user, approved=True)


class NicknameChangeRequestModal(discord.ui.Modal, title="Nickname Change Request"):
    desired_nickname = discord.ui.TextInput(
        label="Desired Nickname",
        placeholder="Your SC in-game name",
        required=True,
        max_length=32,
    )

    async def on_submit(self, interaction: discord.Interaction):
        review_chan_id = await aget_global_preference(NicknameChangeReviewChannelID.import_path)
        if not review_chan_id:
            await interaction.response.send_message(
                "❌ Nickname change review channel is not configured. Contact staff.", ephemeral=True
            )
            return

        review_channel = interaction.client.get_channel(review_chan_id)
        if not review_channel:
            with suppress(discord.NotFound, discord.Forbidden):
                review_channel = await interaction.client.fetch_channel(review_chan_id)
        if not review_channel:
            await interaction.response.send_message(
                "❌ Could not find the review channel. Contact staff.", ephemeral=True
            )
            return

        nickname = self.desired_nickname.value
        embed = discord.Embed(
            title="Nickname Change Request",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Requested By", value=f"{interaction.user.mention} ({interaction.user.display_name})", inline=False)
        embed.add_field(name="Current Nickname", value=interaction.user.display_name, inline=True)
        embed.add_field(name="Desired Nickname", value=nickname, inline=True)
        embed.set_footer(text=f"User ID: {interaction.user.id}")
        embed.timestamp = discord.utils.utcnow()

        view = NicknameRequestView()
        await review_channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            "✅ Your nickname change request has been submitted for review.", ephemeral=True
        )


async def _handle_approve(
    interaction: discord.Interaction, requester_id: int, new_nickname: str
):
    # Defer edit immediately so the interaction doesn't expire while we do async work
    await interaction.response.defer()

    member = interaction.guild.get_member(requester_id)
    if not member:
        with suppress(discord.NotFound):
            member = await interaction.guild.fetch_member(requester_id)
    if not member:
        await interaction.followup.send("❌ Member not found.", ephemeral=True)
        return

    old_nickname = member.display_name
    try:
        await member.edit(nick=new_nickname)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Bot cannot change that member's nickname. They may have a higher role than the bot, or are the server owner.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Failed to change nickname: {e}", ephemeral=True)
        return

    embed = discord.Embed(
        title="Nickname Approved",
        description=f"{member.mention}'s nickname has been changed.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Before", value=old_nickname, inline=True)
    embed.add_field(name="After", value=new_nickname, inline=True)
    embed.add_field(name="Approved By", value=interaction.user.mention, inline=False)
    embed.timestamp = discord.utils.utcnow()

    await interaction.message.edit(embed=embed, view=None)

    await _send_log(interaction.client, interaction.guild, member, old_nickname, new_nickname, interaction.user, approved=True)


async def _handle_deny(interaction: discord.Interaction, requester_id: int):
    await interaction.response.defer()

    member = interaction.guild.get_member(requester_id)
    display = f"<@{requester_id}>"
    desired = "Unknown"
    if member:
        display = member.mention
    try:
        fields = interaction.message.embeds[0].fields
        desired = next((f.value for f in fields if f.name == "Desired Nickname"), "Unknown")
    except Exception:
        pass

    embed = discord.Embed(
        title="Nickname Request Denied",
        description=f"Request by {display} was denied by {interaction.user.mention}.",
        color=discord.Color.red(),
    )
    embed.add_field(name="Requested Nickname", value=desired, inline=False)
    embed.timestamp = discord.utils.utcnow()

    await interaction.message.edit(embed=embed, view=None)

    log_chan_id = await aget_global_preference(NicknameChangeLogChannelID.import_path)
    if log_chan_id:
        log_channel = interaction.client.get_channel(log_chan_id)
        if log_channel:
            log_embed = discord.Embed(
                title="Nickname Request Denied",
                description=f"Request by {display} was denied by {interaction.user.mention}.",
                color=discord.Color.red(),
            )
            log_embed.add_field(name="Requested Nickname", value=desired, inline=False)
            log_embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=log_embed)


async def _send_log(client, guild, member, old_nick, new_nick, approver, *, approved: bool):
    log_chan_id = await aget_global_preference(NicknameChangeLogChannelID.import_path)
    if not log_chan_id:
        return
    log_channel = client.get_channel(log_chan_id)
    if not log_channel:
        return

    embed = discord.Embed(
        title="Nickname Changed",
        color=discord.Color.green() if approved else discord.Color.red(),
    )
    embed.add_field(name="Member", value=member.mention, inline=False)
    embed.add_field(name="Before", value=old_nick, inline=True)
    embed.add_field(name="After", value=new_nick, inline=True)
    if approver:
        embed.add_field(name="Changed By", value=approver.mention, inline=False)
    embed.set_footer(text=f"User ID: {member.id}")
    embed.timestamp = discord.utils.utcnow()
    await log_channel.send(embed=embed)


class NicknameChangeCog(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        self.logger = logging.getLogger(__name__)
        # Re-register persistent views so buttons survive restarts
        self.client.add_view(NicknameRequestView())

    def cog_unload(self):
        pass

    @app_commands.command(name="nickname_request", description="Submit a nickname change request for staff review.")
    async def nickname_request(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NicknameChangeRequestModal())

    @app_commands.command(name="nickname_set", description="Directly set a member's nickname (staff only).")
    @app_commands.describe(member="The member to rename", nickname="New nickname to assign")
    @requires_django_perm("org.change_discordrole")
    async def nickname_set(self, interaction: discord.Interaction, member: discord.Member, nickname: str):
        old_nick = member.display_name
        try:
            await member.edit(nick=nickname)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to change that member's nickname.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Nickname Changed",
            color=discord.Color.green(),
        )
        embed.add_field(name="Member", value=member.mention, inline=False)
        embed.add_field(name="Before", value=old_nick, inline=True)
        embed.add_field(name="After", value=nickname, inline=True)
        embed.add_field(name="Changed By", value=interaction.user.mention, inline=False)
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed)

        await _send_log(self.client, interaction.guild, member, old_nick, nickname, interaction.user, approved=True)


async def setup(client: commands.Bot):
    await client.add_cog(NicknameChangeCog(client))
