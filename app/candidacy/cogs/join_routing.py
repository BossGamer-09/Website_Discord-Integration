"""
app/candidacy/cogs/join_routing.py

On-join routing dropdown: "What brings you to BlightVeil?"
Assigns rank/groups for Applicant, External, and Visitor tracks.
Also handles suspicious-user detection and posts alerts.
"""
import logging

import discord
from asgiref.sync import sync_to_async
from django.contrib.auth.models import Group
from django.utils import timezone
from discord import app_commands
from discord.ext import commands

from app.discordauth.models import DiscordUser
from app.preferences.utils import aget_global_preference
from app.main.util.discord_command_checks import requires_django_perm

from ..preferences import (
    JoinRoutingEnabled,
    JoinRoutingDropdownMessage,
    VisitorRoutingRankPK,
    MembershipVisitorRankPK,
    MembershipVisitorPermissionGroups,
    MembershipSuspiciousUserChannelID,
    MembershipReviewChannelID,
    SuspiciousAccountAgeDays,
    SuspiciousNoAvatarAlert,
)
from app.disfunction.preferences import GatehouseRolesChannelID

logger = logging.getLogger(__name__)

ROUTE_APPLICANT = "applicant"
ROUTE_EXTERNAL  = "external"
ROUTE_VISITOR   = "visitor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@sync_to_async
def _ensure_backend_user(discord_id: int, username: str) -> None:
    """Upsert a DiscordUser + OrgPlayer so we have a backend record."""
    try:
        DiscordUser.ensure_with_user(
            discorduid=discord_id,
            access_token=None,
            refresh_token=None,
            access_token_expires=None,
        )
    except Exception as exc:
        logger.warning("ensure_backend_user failed for %s: %s", discord_id, exc)


@sync_to_async
def _assign_rank_and_groups(discord_id: int, rank_pk: int, group_ids_str: str) -> None:
    """Assign OrgRank and permission Groups to the OrgPlayer linked to discord_id."""
    try:
        from app.unifieduser.models import OrgRank
        du = DiscordUser.objects.select_related("user").filter(discorduid=discord_id).first()
        if not du or not du.user:
            return
        player = du.user

        if rank_pk:
            rank = OrgRank.objects.filter(pk=rank_pk).first()
            if rank:
                player.rank = rank
                player.save(update_fields=["rank"])

        if group_ids_str:
            gids = [int(x.strip()) for x in group_ids_str.split(",") if x.strip().isdigit()]
            groups = list(Group.objects.filter(pk__in=gids))
            if groups:
                player.groups.add(*groups)
    except Exception as exc:
        logger.error("_assign_rank_and_groups failed discord_id=%s: %s", discord_id, exc)


# ---------------------------------------------------------------------------
# Dropdown UI
# ---------------------------------------------------------------------------

class JoinRoutingSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="I want to join BlightVeil",
                description="Start the membership application process",
                emoji="⚔️",
                value=ROUTE_APPLICANT,
            ),
            discord.SelectOption(
                label="I'm from another organization",
                description="External citizen / allied member",
                emoji="🌐",
                value=ROUTE_EXTERNAL,
            ),
            discord.SelectOption(
                label="Just checking things out",
                description="Visitor access — no commitment",
                emoji="👀",
                value=ROUTE_VISITOR,
            ),
        ]
        super().__init__(
            placeholder="Select what brings you here...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle_selection(interaction, self.values[0])


class JoinRoutingView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=86400)  # 24 hours
        self.member = member
        self.add_item(JoinRoutingSelect())

    async def handle_selection(self, interaction: discord.Interaction, route: str):
        # Only the target member may respond
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                "This dropdown is for the new member, not you!", ephemeral=True
            )
            return

        # External citizen opens a modal — must respond before deferring
        if route == ROUTE_EXTERNAL:
            from app.candidacy.cogs.member_application import ExternalCitizenModal, MembershipApplicationCog
            cog = interaction.client.cogs.get("MembershipApplicationCog")
            if cog:
                await interaction.response.send_modal(ExternalCitizenModal(cog))
            else:
                await interaction.response.send_message(
                    "❌ External Citizen applications are temporarily unavailable. Please contact staff.",
                    ephemeral=True,
                )
            self.stop()
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Ensure backend user exists
        await _ensure_backend_user(self.member.id, self.member.name)

        if route == ROUTE_APPLICANT:
            await self._handle_applicant(interaction)
        elif route == ROUTE_VISITOR:
            await self._handle_visitor(interaction)

        # Disable the dropdown after use
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        self.stop()

    async def _handle_applicant(self, interaction: discord.Interaction):
        verification_chan_id = int(
            await aget_global_preference("candidacy__MembershipVerificationChannelID") or 0
        )
        chan_mention = f"<#{verification_chan_id}>" if verification_chan_id else "the verification channel"
        await interaction.followup.send(
            f"Great! Head to {chan_mention} to begin your membership application. "
            f"We'll walk you through the process step by step. Good luck! ⚔️",
            ephemeral=True,
        )
        logger.info("Join routing → APPLICANT for %s (%s)", self.member, self.member.id)

    async def _handle_visitor(self, interaction: discord.Interaction):
        # Prefer the dedicated VisitorRoutingRankPK; fall back to MembershipVisitorRankPK
        rank_pk = int(await aget_global_preference(VisitorRoutingRankPK.import_path) or 0)
        if not rank_pk:
            rank_pk = int(await aget_global_preference(MembershipVisitorRankPK.import_path) or 0)
        group_str = await aget_global_preference(MembershipVisitorPermissionGroups.import_path) or ""
        await _assign_rank_and_groups(self.member.id, rank_pk, group_str)
        await interaction.followup.send(
            "Welcome! You've been set up with **Visitor** access to browse the server. "
            "If you ever decide to apply, head to the verification channel. 👀",
            ephemeral=True,
        )
        logger.info("Join routing → VISITOR for %s (%s)", self.member, self.member.id)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            # We don't hold a reference to the message here — best-effort
            pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class JoinRoutingCog(commands.Cog):
    """Sends the "What brings you to BlightVeil?" dropdown on member join."""

    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        try:
            await self._check_suspicious(member)
        except Exception:
            logger.exception("JoinRoutingCog._check_suspicious failed for %s", member)
        try:
            enabled = await aget_global_preference(JoinRoutingEnabled.import_path)
            if enabled:
                await self._send_routing_dropdown(member)
        except Exception:
            logger.exception("JoinRoutingCog._send_routing_dropdown failed for %s", member)

    async def _send_routing_dropdown(self, member: discord.Member):
        gatehouse_chan_id = int(
            await aget_global_preference(GatehouseRolesChannelID.import_path) or 0
        )
        if not gatehouse_chan_id:
            logger.warning("JoinRoutingCog: GatehouseRolesChannelID not configured, skipping dropdown")
            return

        channel = self.client.get_channel(gatehouse_chan_id)
        if not channel:
            logger.warning("JoinRoutingCog: gatehouse channel %s not in cache", gatehouse_chan_id)
            return

        custom_message = await aget_global_preference(JoinRoutingDropdownMessage.import_path)
        content = custom_message or (
            f"Hey {member.mention}! 👋 Welcome to **BlightVeil**.\n\n"
            f"⚔️ **Join BlightVeil** — You want to become a full member of the organization and go through the membership application process.\n"
            f"🌐 **External Org** — You're visiting from another organization or are an allied member.\n"
            f"👀 **Just Visiting** — You want to browse the server with no commitment.\n\n"
            f"Head to <#{gatehouse_chan_id}> and select the option that fits you to get access."
        )

        view = JoinRoutingView(member)
        await channel.send(content=content, view=view)

    async def _check_suspicious(self, member: discord.Member):
        """Post an alert to staff if the account looks suspicious."""
        try:
            age_threshold = int(await aget_global_preference(SuspiciousAccountAgeDays.import_path) or 0)
            no_avatar_alert = await aget_global_preference(SuspiciousNoAvatarAlert.import_path)

            account_age_days = (timezone.now() - member.created_at).days
            has_default_avatar = member.display_avatar.is_default() if hasattr(member.display_avatar, "is_default") else False

            flags = []
            if age_threshold and account_age_days < age_threshold:
                flags.append(f"Account only **{account_age_days}** days old (threshold: {age_threshold})")
            if no_avatar_alert and has_default_avatar:
                flags.append("No custom avatar")

            if not flags:
                return

            alert_chan_id = int(await aget_global_preference(MembershipSuspiciousUserChannelID.import_path) or 0)
            if not alert_chan_id:
                alert_chan_id = int(await aget_global_preference(MembershipReviewChannelID.import_path) or 0)

            channel = self.client.get_channel(alert_chan_id)
            if not channel:
                return

            embed = discord.Embed(
                title="⚠️ Suspicious Member Joined",
                color=discord.Color.orange(),
                timestamp=timezone.now(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Member", value=f"{member.mention} (`{member}` · `{member.id}`)", inline=False)
            embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
            embed.add_field(name="Flags", value="\n".join(f"• {f}" for f in flags), inline=False)
            embed.set_footer(text="Review and monitor this member as needed.")
            await channel.send(embed=embed)

        except Exception:
            logger.exception("_check_suspicious failed for %s", member)


    @app_commands.command(name="adm_send_join_dropdown", description="Re-send the join routing dropdown to a member [admin]")
    @requires_django_perm("org.can_manage_discord_members")
    async def cmd_resend_dropdown(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        await self._send_routing_dropdown(member)
        await interaction.followup.send(f"✅ Join dropdown re-sent for {member.mention}.", ephemeral=True)


async def setup(client):
    await client.add_cog(JoinRoutingCog(client))
