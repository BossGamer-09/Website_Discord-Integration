"""
app/disfunction/cogs/vc_activity_log.py

Posts join / leave / move notifications in each VC's own text channel.

Events handled:
  join   → new channel:  "✅ X joined"
  leave  → old channel:  "👋 X left"
  move (self)  → new channel: "➡️ X joined from #old"
  move (force) → old channel: "🔀 X was moved to #new by Y"
               + new channel: "🔀 X was moved here by Y"

Requires the bot to have View Audit Log to detect forced moves;
degrades gracefully if missing (treated as self-move).
"""
import asyncio
import logging

import discord
from django.apps import apps
from discord.ext import commands

from app.preferences.utils import aget_global_preference
from app.disfunction.preferences import VCActivityLogEnabled


class VCActivityLogCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.TempVC = apps.get_model("disfunction", "TemporaryVoiceChannel")

    # ------------------------------------------------------------------ #
    # Listener                                                             #
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        # Skip pure state changes (mute / deafen / stream) within the same channel
        if before.channel == after.channel:
            return

        enabled = await aget_global_preference(VCActivityLogEnabled.import_path)
        if not enabled:
            return

        left = before.channel
        joined = after.channel

        if left and joined:
            await self._handle_move(member, left, joined)
        elif joined and await self._is_temp_vc(joined.id):
            await self._safe_send(joined, f"✅ {member.mention} joined")
        elif left and await self._is_temp_vc(left.id):
            await self._safe_send(left, f"👋 {member.mention} left")

    # ------------------------------------------------------------------ #
    # Move handling                                                        #
    # ------------------------------------------------------------------ #

    async def _handle_move(
        self,
        member: discord.Member,
        left: discord.VoiceChannel,
        joined: discord.VoiceChannel,
    ):
        left_is_temp, joined_is_temp = await asyncio.gather(
            self._is_temp_vc(left.id),
            self._is_temp_vc(joined.id),
        )
        if not left_is_temp and not joined_is_temp:
            return

        # Small delay so the audit log entry has time to be written
        await asyncio.sleep(0.5)
        mover = await self._get_mover(member, joined)

        if mover:
            if left_is_temp:
                await self._safe_send(left, f"🔀 {member.mention} was moved to **{joined.name}** by {mover.mention}")
            if joined_is_temp:
                await self._safe_send(joined, f"🔀 {member.mention} was moved here by {mover.mention}")
        else:
            # Self-move: only notify the destination if it's a temp VC
            if joined_is_temp:
                await self._safe_send(joined, f"➡️ {member.mention} joined from **{left.name}**")

    async def _get_mover(
        self,
        member: discord.Member,
        to_channel: discord.VoiceChannel,
    ) -> discord.Member | None:
        """
        Returns the user who force-moved `member` to `to_channel`, or None
        if the member moved themselves (or audit log is inaccessible).
        """
        try:
            async for entry in member.guild.audit_logs(
                limit=10,
                action=discord.AuditLogAction.member_move,
            ):
                age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                if age > 5:
                    break  # entries are chronological; nothing newer will match
                extra_channel = getattr(entry.extra, "channel", None)
                if extra_channel and extra_channel.id == to_channel.id:
                    if entry.user.id != member.id:
                        return entry.user
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    async def _is_temp_vc(self, channel_id: int) -> bool:
        return await self.TempVC.objects.filter(channel_id=channel_id, is_active=True).aexists()

    _SILENT_MENTIONS = discord.AllowedMentions(users=False)

    async def _safe_send(self, channel: discord.VoiceChannel, content: str):
        try:
            await channel.send(content, allowed_mentions=self._SILENT_MENTIONS)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(client):
    await client.add_cog(VCActivityLogCog(client))
