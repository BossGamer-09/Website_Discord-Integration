"""
app/disfunction/cogs/nomination_system.py

Distinction Levels & Nominations system.
All channel IDs are read from preferences — no hardcoded IDs.

Django perms:
  disfunction.can_approve_nominations  — Approve/Deny buttons
  disfunction.can_submit_nominations   — /nominate command (optional; if not set, open to all)
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput, UserSelect
from asgiref.sync import sync_to_async
from django.apps import apps
from django.utils import timezone

from app.preferences.utils import aget_global_preference
from app.main.util.discord_command_checks import requires_django_perm
from app.disfunction.preferences import (
    NominationApprovalChannelID,
    NominationAnnouncementChannelID,
    NominationEmbedChannelID,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Permission helper                                                    #
# ------------------------------------------------------------------ #

async def _has_approve_perm(discord_user_id: int) -> bool:
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return await sync_to_async(
        lambda: User.objects.filter(
            discorduser__discorduid=discord_user_id,
            groups__permissions__codename="can_approve_nominations",
        ).exists()
    )()


# ------------------------------------------------------------------ #
# Modals                                                               #
# ------------------------------------------------------------------ #

class NominationModal(Modal, title="Nomination Form"):
    event_date = TextInput(
        label="Event Name / Date",
        placeholder="e.g., 'Operation Red Harvest' or '2024-01-15'",
        style=discord.TextStyle.short,
        required=True,
        max_length=200,
    )
    reason = TextInput(
        label="Reason for Nomination",
        placeholder="Describe why this user deserves recognition…",
        style=discord.TextStyle.paragraph,
        required=True,
    )

    def __init__(self, nominee: discord.Member, bot, **kwargs):
        super().__init__(**kwargs)
        self.nominee = nominee
        self.bot = bot
        self.timeout = 300

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            Nomination = apps.get_model("disfunction", "Nomination")

            approval_chan_id = await aget_global_preference(NominationApprovalChannelID.import_path)
            approval_chan = self.bot.get_channel(int(approval_chan_id)) if approval_chan_id else None

            if not approval_chan:
                await interaction.followup.send(
                    "❌ Nomination system is not configured yet. Contact staff.", ephemeral=True
                )
                return

            nomination = await Nomination.objects.acreate(
                nominee_id=self.nominee.id,
                nominator_id=interaction.user.id,
                nominee_username=str(self.nominee),
                nominator_username=str(interaction.user),
                event_date=self.event_date.value,
                reason=self.reason.value,
                status="PENDING",
                approval_channel_id=int(approval_chan_id),
            )

            is_self = self.nominee.id == interaction.user.id
            embed = discord.Embed(
                title="🎖️ New Nomination Pending Approval" + (" (Self-Nomination)" if is_self else ""),
                color=discord.Color.orange() if is_self else discord.Color.yellow(),
                timestamp=timezone.now(),
            )
            embed.add_field(name="Nominee",    value=f"{self.nominee.mention} ({self.nominee.id})",         inline=True)
            embed.add_field(name="Nominator",  value=f"{interaction.user.mention} ({interaction.user.id})", inline=True)
            embed.add_field(name="Date/Event", value=self.event_date.value,                                  inline=True)
            embed.add_field(name="Reason",     value=self.reason.value[:500], inline=False)
            embed.set_footer(text=f"Nomination ID: {nomination.id}")

            view = NominationApprovalView(self.bot, nomination.id, self.nominee.id, interaction.user.id)
            msg = await approval_chan.send(embed=embed, view=view)

            # Store message ID so we can re-register the view on restart
            nomination.approval_message_id = msg.id
            await nomination.asave(update_fields=["approval_message_id"])

            await interaction.followup.send(
                f"✅ Nomination for {self.nominee.mention} submitted — pending approval.",
                ephemeral=True,
            )

        except Exception:
            logger.exception("Error submitting nomination")
            await interaction.followup.send("❌ Error submitting nomination. Please try again.", ephemeral=True)


class DenialReasonModal(Modal, title="Denial Reason"):
    reason = TextInput(
        label="Reason for Denial",
        placeholder="Please provide a clear reason…",
        style=discord.TextStyle.paragraph,
        required=True,
    )

    def __init__(self, bot, nomination_id: int, nominee_id: int, nominator_id: int, **kwargs):
        super().__init__(**kwargs)
        self.bot = bot
        self.nomination_id = nomination_id
        self.nominee_id = nominee_id
        self.nominator_id = nominator_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            Nomination = apps.get_model("disfunction", "Nomination")

            nomination = await Nomination.objects.aget(id=self.nomination_id)
            nomination.status = "DENIED"
            nomination.processed_by_id = interaction.user.id
            nomination.processed_by_username = str(interaction.user)
            nomination.processed_at = timezone.now()
            nomination.denial_reason = self.reason.value
            await nomination.asave()

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.title = "❌ Nomination Denied"
            embed.add_field(name="Denial Reason", value=self.reason.value, inline=False)
            await interaction.message.edit(embed=embed, view=None)

            try:
                nominator = await self.bot.fetch_user(self.nominator_id)
                deny_embed = discord.Embed(
                    title="Nomination Denied",
                    description=(
                        f"Your nomination for <@{self.nominee_id}> has been denied.\n\n"
                        f"**Event/Date:** {nomination.event_date}\n"
                        f"**Your Reason:** {nomination.reason[:200]}\n\n"
                        f"**Denial Reason:** {self.reason.value}"
                    ),
                    color=discord.Color.red(),
                    timestamp=timezone.now(),
                )
                await nominator.send(embed=deny_embed)
            except discord.Forbidden:
                pass
            except Exception:
                logger.exception("Error notifying nominator of denial")

            await interaction.followup.send("✅ Nomination denied.", ephemeral=True)

        except Exception:
            logger.exception("Error processing denial")
            await interaction.followup.send("❌ Error processing denial.", ephemeral=True)


# ------------------------------------------------------------------ #
# Views                                                                #
# ------------------------------------------------------------------ #

class UserSelectionView(View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id

    @discord.ui.select(cls=UserSelect, placeholder="Select a user to nominate…", min_values=1, max_values=1)
    async def user_select_callback(self, interaction: discord.Interaction, select: UserSelect):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't for you.", ephemeral=True)
            return

        selected = select.values[0]
        if selected.bot:
            await interaction.response.send_message("You cannot nominate bots.", ephemeral=True)
            return
        if selected.id == interaction.user.id:
            await interaction.response.send_message("You cannot nominate yourself.", ephemeral=True)
            return

        await interaction.response.send_modal(NominationModal(selected, interaction.client))


class NominationApprovalView(View):
    def __init__(self, bot, nomination_id: int, nominee_id: int, nominator_id: int):
        super().__init__(timeout=None)  # persistent
        self.bot = bot
        self.nomination_id = nomination_id
        self.nominee_id = nominee_id
        self.nominator_id = nominator_id

    @discord.ui.button(label="Award Nomination",  style=discord.ButtonStyle.success, emoji="✅",
                       custom_id="nom_approve")
    async def approve_button(self, interaction: discord.Interaction, button: Button):
        if not await _has_approve_perm(interaction.user.id):
            await interaction.response.send_message("❌ You don't have permission to approve nominations.", ephemeral=True)
            return
        await self._process(interaction, "APPROVED")

    @discord.ui.button(label="Grant Recognition", style=discord.ButtonStyle.primary, emoji="🌟",
                       custom_id="nom_recognize")
    async def recognize_button(self, interaction: discord.Interaction, button: Button):
        if not await _has_approve_perm(interaction.user.id):
            await interaction.response.send_message("❌ You don't have permission to approve nominations.", ephemeral=True)
            return
        await self._process(interaction, "RECOGNIZED")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌",
                       custom_id="nom_deny")
    async def deny_button(self, interaction: discord.Interaction, button: Button):
        if not await _has_approve_perm(interaction.user.id):
            await interaction.response.send_message("❌ You don't have permission to deny nominations.", ephemeral=True)
            return
        await interaction.response.send_modal(
            DenialReasonModal(self.bot, self.nomination_id, self.nominee_id, self.nominator_id)
        )

    async def _process(self, interaction: discord.Interaction, status: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            Nomination       = apps.get_model("disfunction", "Nomination")
            UserDistinction  = apps.get_model("disfunction", "UserDistinction")
            DistinctionLevel = apps.get_model("disfunction", "DistinctionLevel")

            nomination = await Nomination.objects.aget(id=self.nomination_id)
            if nomination.status != "PENDING":
                await interaction.followup.send("❌ This nomination has already been processed.", ephemeral=True)
                return

            nomination.status = status
            nomination.processed_by_id = interaction.user.id
            nomination.processed_by_username = str(interaction.user)
            nomination.processed_at = timezone.now()
            await nomination.asave()

            if status == "APPROVED":
                await self._update_distinction(self.nominee_id, interaction.guild, UserDistinction, DistinctionLevel)

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green() if status == "APPROVED" else discord.Color.orange()
            embed.title = f"🎖️ Nomination {'Awarded' if status == 'APPROVED' else 'Recognized'}"
            embed.add_field(name="Processed By", value=interaction.user.mention, inline=True)
            self.clear_items()
            self.stop()
            await interaction.message.edit(embed=embed, view=self)

            await self._send_announcement(interaction, nomination, status)
            await interaction.followup.send(f"✅ Nomination {status.lower()}.", ephemeral=True)

        except Exception:
            logger.exception("Error processing nomination approval")
            await interaction.followup.send("❌ Error processing nomination.", ephemeral=True)

    async def _update_distinction(self, user_id: int, guild: discord.Guild, UserDistinction, DistinctionLevel):
        try:
            ud, created = await UserDistinction.objects.aget_or_create(user_id=user_id)

            if created:
                ud.total_nominations = 1
                ud.last_nomination_date = timezone.now()
                ud.current_streak = 1
                ud.longest_streak = 1
            else:
                prev_date = ud.last_nomination_date
                ud.total_nominations += 1
                if prev_date and (timezone.now() - prev_date).days <= 30:
                    ud.current_streak += 1
                else:
                    ud.current_streak = 1
                ud.last_nomination_date = timezone.now()
                ud.longest_streak = max(ud.longest_streak, ud.current_streak)
                months = max(1, (timezone.now() - ud.created_at).days / 30)
                ud.nomination_rate = ud.total_nominations / months

            await ud.asave()

            new_level = None
            async for lvl in DistinctionLevel.objects.filter(
                nominations_required__lte=ud.total_nominations
            ).order_by('-level'):
                new_level = lvl
                break

            if new_level and new_level.level != ud.current_level:
                old_level = ud.current_level
                ud.current_level = new_level.level
                ud.last_level_up = timezone.now()
                await ud.asave()

                member = guild.get_member(user_id)
                if member:
                    async for lvl in DistinctionLevel.objects.all():
                        role = guild.get_role(lvl.role_id)
                        if role and role in member.roles and lvl.level != new_level.level:
                            await member.remove_roles(role)
                    new_role = guild.get_role(new_level.role_id)
                    if new_role:
                        await member.add_roles(new_role)
                    await self._send_level_up(member, old_level, new_level, ud)

        except Exception:
            logger.exception("Error updating user distinction for %s", user_id)

    async def _send_level_up(self, member: discord.Member, old_level: int, new_level, ud):
        try:
            chan_id = await aget_global_preference(NominationAnnouncementChannelID.import_path)
            chan = member.guild.get_channel(int(chan_id)) if chan_id else None
            if not chan:
                return
            embed = discord.Embed(
                title=f"🏆 Distinction Level {new_level.level} Attained!",
                description=(
                    f"Congratulations {member.mention}!\n\n"
                    f"**Promoted from Level {old_level} to Level {new_level.level}**\n"
                    f"**Role:** {new_level.role_name}\n"
                    f"**Total Nominations:** {ud.total_nominations}\n\n"
                    f"*{new_level.description or ''}*"
                ),
                color=discord.Color.from_str(new_level.color) if new_level.color else discord.Color.purple(),
                timestamp=timezone.now(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await chan.send(embed=embed)
        except Exception:
            logger.exception("Error sending level-up announcement for %s", member.id)

    async def _send_announcement(self, interaction: discord.Interaction, nomination, status: str):
        try:
            chan_id = await aget_global_preference(NominationAnnouncementChannelID.import_path)
            chan = interaction.guild.get_channel(int(chan_id)) if chan_id else None
            if not chan:
                return

            nominee = interaction.guild.get_member(self.nominee_id)
            if not nominee:
                return

            if status == "APPROVED":
                embed = discord.Embed(title="🎖️ Nomination Awarded!", color=discord.Color.green(), timestamp=nomination.processed_at)
                embed.description = (
                    f"{nominee.mention}, you have received an **official nomination**!\n\n"
                    f"**For:** {nomination.reason}\n"
                    f"**During:** {nomination.event_date}"
                )
            else:
                embed = discord.Embed(title="🌟 Recognition Granted!", color=discord.Color.orange(), timestamp=nomination.processed_at)
                embed.description = (
                    f"{nominee.mention}, you've been **recognized** for outstanding contribution!\n\n"
                    f"**For:** {nomination.reason}\n"
                    f"**During:** {nomination.event_date}"
                )

            await chan.send(embed=embed)
        except Exception:
            logger.exception("Error sending nomination announcement")


class CheckNominationsView(View):
    def __init__(self, author_id: int, bot):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.bot = bot

    @discord.ui.button(label="Check My Nominations", style=discord.ButtonStyle.secondary, emoji="📊")
    async def check_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            Nomination       = apps.get_model("disfunction", "Nomination")
            UserDistinction  = apps.get_model("disfunction", "UserDistinction")
            DistinctionLevel = apps.get_model("disfunction", "DistinctionLevel")

            nominations = [
                n async for n in Nomination.objects.filter(
                    nominee_id=interaction.user.id,
                    status__in=["APPROVED", "RECOGNIZED"],
                ).order_by("-processed_at")
            ]

            try:
                ud = await UserDistinction.objects.aget(user_id=interaction.user.id)
            except UserDistinction.DoesNotExist:
                ud = None

            summary = discord.Embed(
                title="📊 Your Nominations Summary",
                color=discord.Color.blue(),
                timestamp=timezone.now(),
            )

            current_level = ud.current_level if ud else 0
            next_level = await DistinctionLevel.objects.filter(
                level__gt=current_level
            ).order_by("level").afirst()

            summary.add_field(name="Current Level",      value=str(current_level),    inline=True)
            summary.add_field(name="Total Nominations",  value=str(len(nominations)), inline=True)
            if next_level:
                needed = max(0, next_level.nominations_required - len(nominations))
                summary.add_field(name="To Next Level", value=f"{needed} more (Level {next_level.level})", inline=True)
            if ud and ud.current_streak:
                summary.add_field(name="Streak", value=f"{ud.current_streak} months", inline=True)

            embeds = [summary]
            for i, nom in enumerate(nominations[:9]):
                ne = discord.Embed(
                    title=f"🏅 Nomination #{i + 1}",
                    color=discord.Color.green() if nom.status == "APPROVED" else discord.Color.orange(),
                    timestamp=nom.processed_at or nom.created_at,
                )
                ne.add_field(name="Date/Event", value=nom.event_date, inline=True)
                ne.add_field(name="Type",       value=nom.status.title(), inline=True)
                ne.add_field(name="Reason",     value=nom.reason[:300], inline=False)
                embeds.append(ne)

            await interaction.followup.send(embeds=embeds, ephemeral=True)

        except Exception:
            logger.exception("Error fetching nominations for %s", interaction.user.id)
            await interaction.followup.send("❌ Error fetching nominations.", ephemeral=True)


class DistinctionEmbedView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Nominate Someone",     style=discord.ButtonStyle.primary,   emoji="🎖️")
    async def nominate_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "Select a user to nominate:",
            view=UserSelectionView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Check My Nominations", style=discord.ButtonStyle.secondary, emoji="📊")
    async def check_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "Fetching your nomination data…",
            view=CheckNominationsView(interaction.user.id, self.bot),
            ephemeral=True,
        )


# ------------------------------------------------------------------ #
# Cog                                                                  #
# ------------------------------------------------------------------ #

class NominationSystemCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.embed_message_id = None

    async def cog_load(self):
        """Re-register persistent approval views for all pending nominations on startup."""
        try:
            Nomination = apps.get_model("disfunction", "Nomination")
            async for nom in Nomination.objects.filter(
                status="PENDING", approval_message_id__isnull=False
            ):
                view = NominationApprovalView(
                    self.bot, nom.id, nom.nominee_id, nom.nominator_id
                )
                self.bot.add_view(view, message_id=nom.approval_message_id)
            logger.info("NominationSystemCog: re-registered pending nomination views")
        except Exception:
            logger.exception("Error re-registering nomination views on startup")

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            chan_id = await aget_global_preference(NominationEmbedChannelID.import_path)
            if not chan_id:
                return
            chan = self.bot.get_channel(int(chan_id))
            if not chan:
                return

            async for msg in chan.history(limit=50):
                if msg.author == self.bot.user and msg.embeds and "Distinction Levels" in (msg.embeds[0].title or ""):
                    try:
                        await msg.delete()
                    except Exception:
                        pass

            embed = discord.Embed(
                title="🎖️ Distinction Levels & Nominations System",
                description=(
                    "**Receiving nominations increases your Distinction Level!**\n\n"
                    "Your Distinction Level reflects your contributions to BlightVeil — "
                    "your standing, dedication, and impact within the organisation.\n\n"
                    "**How it works:**\n"
                    "• **Nominate** members for outstanding contributions\n"
                    "• **Nominations** are reviewed by staff\n"
                    "• **Approved nominations** count toward your Distinction Level\n"
                    "• **Higher levels** unlock special roles and recognition\n\n"
                    "**Level Requirements:**\n"
                    "• **Level 1:** 3 · **Level 2:** 7 · **Level 3:** 15 · **Level 4:** 25 · **Level 5:** 35\n"
                    "• **Level 6:** 50 · **Level 7:** 65 · **Level 8:** 85 · **Level 9:** 105 · **Level 10:** 150+"
                ),
                color=discord.Color.blue(),
                timestamp=timezone.now(),
            )
            embed.set_footer(text="Use the buttons below to nominate or check your nominations.")

            msg = await chan.send(embed=embed, view=DistinctionEmbedView(self.bot))
            self.embed_message_id = msg.id

        except Exception:
            logger.exception("Error sending nomination system embed")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        try:
            DistinctionLevel = apps.get_model("disfunction", "DistinctionLevel")
            level = await DistinctionLevel.objects.filter(role_id=role.id).afirst()
            if level:
                logger.warning("Distinction role %s (Level %s) was deleted!", role.name, level.level)
        except Exception:
            logger.exception("Error handling role deletion in NominationSystemCog")

    # ------------------------------------------------------------------ #
    # /nominate slash command                                              #
    # ------------------------------------------------------------------ #

    @app_commands.command(name="nominate", description="Nominate a member for a distinction award")
    @app_commands.describe(member="The member you want to nominate")
    @requires_django_perm("disfunction.can_submit_nominations")
    async def nominate_slash(self, interaction: discord.Interaction, member: discord.Member):
        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You cannot nominate yourself.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("❌ You cannot nominate a bot.", ephemeral=True)
            return
        await interaction.response.send_modal(NominationModal(nominee=member, bot=self.bot))

    @app_commands.command(name="self_nominate", description="Submit a self-nomination for a distinction award")
    @requires_django_perm("disfunction.can_self_nominate")
    async def self_nominate_slash(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(interaction.user.id) or interaction.user
        await interaction.response.send_modal(
            NominationModal(nominee=member, bot=self.bot, title="Self-Nomination Form")
        )

    # ------------------------------------------------------------------ #
    # /nomination_setup — staff post/refresh the embed portal             #
    # ------------------------------------------------------------------ #

    @app_commands.command(name="nomination_setup", description="[Staff] Post or refresh the nominations embed")
    @requires_django_perm("disfunction.can_approve_nominations")
    async def nomination_setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            chan_id = await aget_global_preference(NominationEmbedChannelID.import_path)
            if not chan_id:
                await interaction.followup.send("❌ `NominationEmbedChannelID` preference is not set.", ephemeral=True)
                return
            chan = self.bot.get_channel(int(chan_id))
            if not chan:
                await interaction.followup.send("❌ Channel not found.", ephemeral=True)
                return

            async for msg in chan.history(limit=50):
                if msg.author == self.bot.user and msg.embeds and "Distinction Levels" in (msg.embeds[0].title or ""):
                    try:
                        await msg.delete()
                    except Exception:
                        pass

            embed = discord.Embed(
                title="🎖️ Distinction Levels & Nominations System",
                description=(
                    "**Receiving nominations increases your Distinction Level!**\n\n"
                    "Your Distinction Level reflects your contributions to BlightVeil — "
                    "your standing, dedication, and impact within the organisation.\n\n"
                    "**How it works:**\n"
                    "• **Nominate** members for outstanding contributions\n"
                    "• **Nominations** are reviewed by staff\n"
                    "• **Approved nominations** count toward your Distinction Level\n"
                    "• **Higher levels** unlock special roles and recognition\n\n"
                    "**Level Requirements:**\n"
                    "• **Level 1:** 3 · **Level 2:** 7 · **Level 3:** 15 · **Level 4:** 25 · **Level 5:** 35\n"
                    "• **Level 6:** 50 · **Level 7:** 65 · **Level 8:** 85 · **Level 9:** 105 · **Level 10:** 150+"
                ),
                color=discord.Color.blue(),
                timestamp=timezone.now(),
            )
            embed.set_footer(text="Use the buttons below to nominate or check your nominations.")
            await chan.send(embed=embed, view=DistinctionEmbedView(self.bot))
            await interaction.followup.send("✅ Nominations portal posted.", ephemeral=True)
        except Exception:
            logger.exception("Error in nomination_setup")
            await interaction.followup.send("❌ Error posting nominations portal.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(NominationSystemCog(bot))
