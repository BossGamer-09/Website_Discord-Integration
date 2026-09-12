"""
app/org/cogs/org_announce_cog.py

Freeform org announcements with audience targeting and optional approval flow.

/announce post   — write a message, pick audience (full org / discipline / role), submit
/announce pending — [staff] list pending announcements awaiting approval

Approval flow (when OrgAnnounceRequireApproval is ON):
  1. Leader runs /announce post, fills modal
  2. Embed + Approve/Deny buttons posted to OrgAnnounceApprovalChannelID
  3. Approver clicks Approve → message sent to target channel with optional role ping
  4. Approver clicks Deny   → submitter notified via DM

When approval is OFF the message is sent immediately.
"""
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from asgiref.sync import sync_to_async
from django.utils import timezone

from app.main.util.discord_command_checks import requires_django_perm
from app.preferences.utils import aget_global_preference
from app.org.preferences import (
    OrgAnnounceDefaultChannelID,
    OrgAnnounceApprovalChannelID,
    OrgAnnounceRequireApproval,
    OrgAnnounceAuditLogChannelID,
)

logger = logging.getLogger(__name__)

# Audience options shown in the /announce post command
_AUDIENCE_FULL_ORG = "Full Org"


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------

class AnnounceModal(discord.ui.Modal, title="Write Announcement"):
    message_body = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=2000,
        placeholder="Your announcement...",
    )

    def __init__(self, cog, audience: str, ping_role_id: Optional[int], target_channel_id: Optional[int]):
        super().__init__(timeout=300)
        self.cog             = cog
        self.audience        = audience
        self.ping_role_id    = ping_role_id
        self.target_channel_id = target_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        body = self.message_body.value

        require_approval = await aget_global_preference(OrgAnnounceRequireApproval.import_path)

        if require_approval:
            await self.cog._post_for_approval(
                interaction, body, self.audience, self.ping_role_id, self.target_channel_id
            )
            await interaction.followup.send(
                "✅ Announcement submitted for approval. You'll be notified when it's reviewed.",
                ephemeral=True,
            )
        else:
            await self.cog._send_announcement(
                interaction.guild, body, self.ping_role_id, self.target_channel_id
            )
            await interaction.followup.send("✅ Announcement posted!", ephemeral=True)
            await _write_audit_log(
                bot=interaction.client,
                decision="SENT",
                submitter_id=interaction.user.id,
                reviewer_id=None,
                audience=self.audience,
                body_preview=body[:500],
                target_channel_id=self.target_channel_id,
                guild=interaction.guild,
            )


# ---------------------------------------------------------------------------
# Approval view
# ---------------------------------------------------------------------------

class AnnounceApprovalView(discord.ui.View):
    def __init__(self, bot, body: str, audience: str, ping_role_id: Optional[int],
                 target_channel_id: Optional[int], submitter_id: int):
        super().__init__(timeout=None)
        self.bot               = bot
        self.body              = body
        self.audience          = audience
        self.ping_role_id      = ping_role_id
        self.target_channel_id = target_channel_id
        self.submitter_id      = submitter_id

    @discord.ui.button(label="Approve & Send", style=discord.ButtonStyle.success, emoji="✅",
                       custom_id="org_announce_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        await _do_send(guild, self.body, self.ping_role_id, self.target_channel_id)

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = "✅ Announcement Approved & Sent"
        embed.add_field(name="Approved By", value=interaction.user.mention, inline=True)
        self.clear_items()
        self.stop()
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send("✅ Sent.", ephemeral=True)

        await _write_audit_log(
            bot=self.bot,
            decision="APPROVED",
            submitter_id=self.submitter_id,
            reviewer_id=interaction.user.id,
            audience=self.audience,
            body_preview=self.body[:500],
            target_channel_id=self.target_channel_id,
            guild=interaction.guild,
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌",
                       custom_id="org_announce_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            AnnounceDenialModal(self.bot, self.submitter_id, interaction.message)
        )


class AnnounceDenialModal(discord.ui.Modal, title="Denial Reason"):
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        required=True,
    )

    def __init__(self, bot, submitter_id: int, approval_message: discord.Message):
        super().__init__(timeout=300)
        self.bot              = bot
        self.submitter_id     = submitter_id
        self.approval_message = approval_message

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = self.approval_message.embeds[0]
        audience = _get_field(embed, "Audience")
        body_preview = (embed.description or "")[:500]
        target_channel_id = _parse_channel_field(embed, "Target Channel")

        embed.color = discord.Color.red()
        embed.title = "❌ Announcement Denied"
        embed.add_field(name="Denied By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason",    value=self.reason.value[:500],  inline=False)
        await self.approval_message.edit(embed=embed, view=None)

        try:
            submitter = await self.bot.fetch_user(self.submitter_id)
            dm = discord.Embed(
                title="Your Announcement Was Denied",
                description=f"**Reason:** {self.reason.value}",
                color=discord.Color.red(),
            )
            await submitter.send(embed=dm)
        except discord.Forbidden:
            pass
        except Exception:
            logger.exception("AnnounceDenialModal: failed to DM submitter %s", self.submitter_id)

        await interaction.followup.send("✅ Denied and submitter notified.", ephemeral=True)

        await _write_audit_log(
            bot=self.bot,
            decision="DENIED",
            submitter_id=self.submitter_id,
            reviewer_id=interaction.user.id,
            audience=audience,
            body_preview=body_preview,
            denial_reason=self.reason.value,
            target_channel_id=target_channel_id,
            guild=interaction.guild,
        )


# ---------------------------------------------------------------------------
# Audit log helpers
# ---------------------------------------------------------------------------

def _get_field(embed: discord.Embed, name: str) -> str:
    for f in embed.fields:
        if f.name == name:
            return f.value or ""
    return ""


def _parse_channel_field(embed: discord.Embed, name: str):
    raw = _get_field(embed, name)
    if raw.startswith("<#") and raw.endswith(">"):
        try:
            return int(raw[2:-1])
        except ValueError:
            pass
    return None


async def _write_audit_log(
    *,
    bot,
    decision: str,
    submitter_id: int,
    reviewer_id: int | None,
    audience: str,
    body_preview: str,
    denial_reason: str = "",
    target_channel_id: int | None = None,
    guild: discord.Guild | None = None,
):
    from asgiref.sync import sync_to_async
    from app.org.models import AnnounceAuditLog

    try:
        await sync_to_async(AnnounceAuditLog.objects.create)(
            decision=decision,
            submitter_id=submitter_id,
            reviewer_id=reviewer_id,
            audience=audience,
            body_preview=body_preview,
            denial_reason=denial_reason,
            target_channel_id=target_channel_id,
        )
    except Exception:
        logger.exception("_write_audit_log: DB write failed")

    # Also post to the audit log Discord channel if configured
    try:
        chan_id = int(await aget_global_preference(OrgAnnounceAuditLogChannelID.import_path) or 0)
        if not chan_id or not guild:
            return
        chan = guild.get_channel(chan_id)
        if not chan:
            return
        color   = discord.Color.green() if decision == "APPROVED" else discord.Color.red()
        emoji   = "✅" if decision == "APPROVED" else "❌"
        embed   = discord.Embed(title=f"{emoji} Announcement {decision.title()}", color=color, timestamp=discord.utils.utcnow())
        embed.add_field(name="Submitter",  value=f"<@{submitter_id}>",               inline=True)
        embed.add_field(name="Reviewer",   value=f"<@{reviewer_id}>" if reviewer_id else "—", inline=True)
        embed.add_field(name="Audience",   value=audience or "—",                    inline=True)
        if body_preview:
            embed.add_field(name="Preview", value=body_preview[:300], inline=False)
        if denial_reason:
            embed.add_field(name="Denial Reason", value=denial_reason[:300], inline=False)
        await chan.send(embed=embed)
    except Exception:
        logger.exception("_write_audit_log: Discord post failed")


# ---------------------------------------------------------------------------
# Shared send helper (sync-safe, called from async context)
# ---------------------------------------------------------------------------

async def _do_send(guild: discord.Guild, body: str, ping_role_id: Optional[int],
                   target_channel_id: Optional[int]) -> Optional[discord.Message]:
    chan_id = target_channel_id or await aget_global_preference(OrgAnnounceDefaultChannelID.import_path)
    if not chan_id:
        logger.error("_do_send: no target channel configured")
        return None

    chan = guild.get_channel(int(chan_id))
    if not chan:
        logger.error("_do_send: channel %s not found in guild", chan_id)
        return None

    content = body
    if ping_role_id:
        role = guild.get_role(int(ping_role_id))
        if role:
            content = f"{role.mention}\n\n{body}"

    try:
        return await chan.send(content, allowed_mentions=discord.AllowedMentions(roles=True))
    except Exception:
        logger.exception("_do_send: failed to post to channel %s", chan_id)
        return None


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class OrgAnnounceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def cog_unload(self):
        pass

    announce = app_commands.Group(name="announce", description="Org announcement commands")

    @announce.command(name="post", description="Post an org announcement")
    @app_commands.describe(
        audience="Who to target — pick a discipline/role or Full Org",
        channel="Override the default announcement channel (optional)",
    )
    @requires_django_perm("org.can_manage_discord_members")
    async def cmd_post(
        self,
        interaction: discord.Interaction,
        audience: str = _AUDIENCE_FULL_ORG,
        channel: Optional[discord.TextChannel] = None,
    ):
        # Resolve ping role from audience string
        ping_role_id: Optional[int] = None
        if audience != _AUDIENCE_FULL_ORG:
            role = discord.utils.find(
                lambda r: audience.lower() in r.name.lower(), interaction.guild.roles
            )
            ping_role_id = role.id if role else None

        target_channel_id = channel.id if channel else None
        await interaction.response.send_modal(
            AnnounceModal(self, audience, ping_role_id, target_channel_id)
        )

    @cmd_post.autocomplete("audience")
    async def audience_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = [_AUDIENCE_FULL_ORG]
        # Pull from ActivityPingRole labels (discipline roles already in DB)
        def _roles():
            from app.org.models import ActivityPingRole
            return list(
                ActivityPingRole.objects.filter(is_active=True)
                .values_list("label", flat=True)
                .order_by("category", "display_order")[:24]
            )
        labels = await sync_to_async(_roles)()
        choices += labels
        if current:
            choices = [c for c in choices if current.lower() in c.lower()]
        return [app_commands.Choice(name=c, value=c) for c in choices[:25]]

    @announce.command(name="pending", description="[Staff] List pending announcements awaiting approval")
    @requires_django_perm("org.can_manage_discord_members")
    async def cmd_pending(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        approval_chan_id = await aget_global_preference(OrgAnnounceApprovalChannelID.import_path)
        if not approval_chan_id:
            await interaction.followup.send("No approval channel configured (`OrgAnnounceApprovalChannelID`).", ephemeral=True)
            return

        chan = interaction.guild.get_channel(int(approval_chan_id))
        if not chan:
            await interaction.followup.send("Approval channel not found.", ephemeral=True)
            return

        pending = []
        async for msg in chan.history(limit=50):
            if msg.author.id == self.bot.user.id and msg.embeds:
                embed = msg.embeds[0]
                if embed.title and "Pending" in embed.title and msg.components:
                    pending.append((msg.id, embed))

        if not pending:
            await interaction.followup.send("No pending announcements.", ephemeral=True)
            return

        lines = [
            f"**#{msg_id}** — {embed.description[:80] if embed.description else '(no preview)'}…"
            for msg_id, embed in pending[:10]
        ]
        result_embed = discord.Embed(
            title=f"📋 Pending Announcements ({len(pending)})",
            description="\n".join(lines),
            color=0xF59E0B,
        )
        result_embed.set_footer(text=f"Review in #{chan.name}")
        await interaction.followup.send(embed=result_embed, ephemeral=True)

    # Internal helpers called by modal/view

    async def _post_for_approval(
        self, interaction: discord.Interaction, body: str,
        audience: str, ping_role_id: Optional[int], target_channel_id: Optional[int]
    ):
        approval_chan_id = await aget_global_preference(OrgAnnounceApprovalChannelID.import_path)
        if not approval_chan_id:
            # No approval channel — send direct
            await _do_send(interaction.guild, body, ping_role_id, target_channel_id)
            return

        chan = interaction.guild.get_channel(int(approval_chan_id))
        if not chan:
            await _do_send(interaction.guild, body, ping_role_id, target_channel_id)
            return

        embed = discord.Embed(
            title="📣 Announcement Pending Approval",
            description=body[:2000],
            color=discord.Color.yellow(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Submitted By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Audience",     value=audience,                 inline=True)
        if ping_role_id:
            embed.add_field(name="Ping Role", value=f"<@&{ping_role_id}>",  inline=True)
        if target_channel_id:
            embed.add_field(name="Target Channel", value=f"<#{target_channel_id}>", inline=True)

        view = AnnounceApprovalView(
            self.bot, body, audience, ping_role_id, target_channel_id, interaction.user.id
        )
        await chan.send(embed=embed, view=view)

    async def _send_announcement(
        self, guild: discord.Guild, body: str,
        ping_role_id: Optional[int], target_channel_id: Optional[int]
    ):
        await _do_send(guild, body, ping_role_id, target_channel_id)


async def setup(bot):
    await bot.add_cog(OrgAnnounceCog(bot))
