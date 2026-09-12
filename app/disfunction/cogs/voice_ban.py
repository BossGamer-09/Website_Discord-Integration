"""
app/disfunction/cogs/voice_ban.py

Manages voice bans (RED / PROTECTED channel restrictions).
Receives channel-type change events via Redis Pub/Sub instead of the
deprecated asyncio.run_coroutine_threadsafe signal approach.
"""
import asyncio
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.utils import format_dt
from datetime import timedelta
from asgiref.sync import sync_to_async
from django.apps import apps
from django.utils import timezone

from app.preferences.utils import aget_global_preference
from app.main.util.discord_command_checks import requires_django_perm
from app.disfunction.preferences import VoiceBanLogChannelID

PROTECTED_TYPES = {"RED", "PROTECTED", "STAFF", "LEADER"}
MOVE_TO_VC_ID_ON_BAN = None  # None = disconnect from VC


class VoiceBanCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.VoiceBan = apps.get_model("disfunction", "VoiceBan")
        self.TemporaryVoiceChannel = apps.get_model("disfunction", "TemporaryVoiceChannel")
        self.mention_reply_only = discord.AllowedMentions(users=False, roles=False, replied_user=True)
        self._log_channel = None
        self.check_bans.start()

    def cog_unload(self):
        self.check_bans.cancel()

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    async def _get_log_chan(self):
        if self._log_channel is None:
            chan_id = await aget_global_preference(VoiceBanLogChannelID.import_path)
            if not chan_id:
                return None
            self._log_channel = self.client.get_channel(int(chan_id)) or await self.client.fetch_channel(int(chan_id))
        return self._log_channel

    async def get_member_by_id(self, guild: discord.Guild, user_id: int) -> discord.Member:
        try:
            return guild.get_member(user_id) or await guild.fetch_member(user_id)
        except discord.NotFound:
            self.logger.error("User %s not in guild %s", user_id, guild.id)
            raise

    async def get_voice_channel(self, channel_id: int) -> discord.VoiceChannel:
        ch = self.client.get_channel(channel_id)
        if ch is None:
            ch = await self.client.fetch_channel(channel_id)
        if not isinstance(ch, discord.VoiceChannel):
            raise ValueError(f"Channel {channel_id} is not a VoiceChannel")
        return ch

    async def _restrict(self, channel: discord.VoiceChannel, member: discord.Member) -> None:
        ow = channel.overwrites_for(member)
        ow.connect = False
        await channel.set_permissions(member, overwrite=ow, reason="VoiceBan restrict")
        self.logger.info("Restricted %s from %s", member.display_name, channel.name)

    async def _unrestrict(self, channel: discord.VoiceChannel, member: discord.Member) -> None:
        ow = channel.overwrites_for(member)
        if ow.connect is None:
            return
        ow.connect = None
        if ow.is_empty():
            await channel.set_permissions(member, overwrite=None, reason="VoiceBan unrestrict")
        else:
            await channel.set_permissions(member, overwrite=ow, reason="VoiceBan unrestrict")
        self.logger.info("Unrestricted %s from %s", member.display_name, channel.name)

    async def apply_all_bans_to_channel(self, channel: discord.VoiceChannel) -> None:
        """Apply all active voice bans to a newly-created protected channel."""
        guild = channel.guild
        async for ban in self.VoiceBan.objects.filter(is_expired=False, kind=self.VoiceBan.Kind.RED):
            try:
                member = await self.get_member_by_id(guild, ban.user_id)
                await self._restrict(channel, member)
            except Exception:
                self.logger.exception("apply_all_bans_to_channel: failed for user %s on %s", ban.user_id, channel.id)

    async def _active_protected_channels(self) -> list[discord.VoiceChannel]:
        channels = []
        async for tvc in self.TemporaryVoiceChannel.objects.filter(
            channel_type__in=list(PROTECTED_TYPES), is_active=True
        ):
            try:
                channels.append(await self.get_voice_channel(tvc.channel_id))
            except Exception:
                self.logger.exception("Could not fetch VC %s", tvc.channel_id)
        return channels

    # ------------------------------------------------------------------ #
    # Ban / Unban logic                                                    #
    # ------------------------------------------------------------------ #

    async def ban_user(
        self, member: discord.Member, reason: str = None, comment: str = None, invoker: discord.Member = None
    ) -> bool:
        if await self.VoiceBan.objects.filter(user_id=member.id, is_expired=False).aexists():
            self.logger.info("Ban skipped — %s already banned", member.display_name)
            return False

        expires_at = timezone.now() + timedelta(days=60)
        ban = self.VoiceBan(
            user_id=member.id,
            kind=self.VoiceBan.Kind.RED,
            expires_at=expires_at,
            reason=f"60-Day Voice Ban: {reason}" if reason else "60-Day Voice Ban",
            comment=comment,
        )
        ban._history_user = invoker.id if invoker else None
        await ban.asave()

        protected_channels = await self._active_protected_channels()
        await asyncio.gather(*[self._restrict(ch, member) for ch in protected_channels])

        if member.voice and member.voice.channel in protected_channels:
            kick_to = None
            if MOVE_TO_VC_ID_ON_BAN:
                kick_to = await self.get_voice_channel(MOVE_TO_VC_ID_ON_BAN)
            await member.move_to(kick_to, reason="VoiceBan kick")

        self.logger.info("Banned %s until %s (invoker: %s)", member.display_name, expires_at, invoker)

        reason_fmt = f"`{reason}`" if reason else "None"
        reason_user_fmt = f", reason: `{reason}`" if reason else ""
        log_chan = await self._get_log_chan()
        try:
            invoker_mention = invoker.mention if invoker else "System"
            t1 = log_chan.send(
                f"{member.mention} **Banned** by {invoker_mention} from protected VCs for 60 days, "
                f"expires {format_dt(expires_at, style='R')}, reason: {reason_fmt}",
                allowed_mentions=self.mention_reply_only,
            )
            t2 = self.client.send_private_notification(
                member,
                f"You have been **Restricted** from **all protected Voice Channels** for 60 days, "
                f"expires {format_dt(expires_at, style='R')}{reason_user_fmt}",
            )
            await asyncio.gather(t1, t2)
        except Exception:
            self.logger.exception("Error sending ban log for %s", member.id)

        return True

    async def unban_user(
        self, member: discord.Member, comment: str = None, invoker: discord.Member = None
    ) -> bool:
        if not await self.VoiceBan.objects.filter(user_id=member.id, is_expired=False).aexists():
            self.logger.info("Unban skipped — %s not banned", member.display_name)
            return False

        protected_channels = await self._active_protected_channels()
        await asyncio.gather(*[self._unrestrict(ch, member) for ch in protected_channels])

        async for ban in self.VoiceBan.objects.filter(user_id=member.id, is_expired=False):
            if comment:
                ban.comment = "\n".join(filter(None, [ban.comment, comment]))
                ban._history_user = invoker.id if invoker else None
                await ban.asave()
            await ban.adelete()

        self.logger.info("Unbanned %s (invoker: %s)", member.display_name, invoker)

        comment_fmt = f"`{comment}`" if comment else "None"
        log_chan = await self._get_log_chan()
        try:
            if invoker:
                t1 = log_chan.send(
                    f"{member.mention} **Unbanned** by {invoker.mention}, comment: {comment_fmt}",
                    allowed_mentions=self.mention_reply_only,
                )
            else:
                t1 = log_chan.send(
                    f"{member.mention} **Unbanned** (automatic expiry), comment: {comment_fmt}",
                    allowed_mentions=self.mention_reply_only,
                )
            t2 = self.client.send_private_notification(
                member, "You have been **Unrestricted** from all protected Voice Channels."
            )
            await asyncio.gather(t1, t2)
        except Exception:
            self.logger.exception("Error sending unban log for %s", member.id)

        return True

    # ------------------------------------------------------------------ #
    # Redis Pub/Sub — receive TempVCTypeChanged events                    #
    # ------------------------------------------------------------------ #

    async def on_vc_type_changed(self, data: dict) -> None:
        """Called by the bot's pubsub listener when a VC type changes."""
        action = data.get("action")
        channel_id = data.get("channel_id")

        if not action or not channel_id:
            return

        try:
            channel = await self.get_voice_channel(channel_id)
        except Exception:
            self.logger.exception("on_vc_type_changed: could not get channel %s", channel_id)
            return

        async for ban in self.VoiceBan.objects.filter(is_expired=False, kind=self.VoiceBan.Kind.RED):
            try:
                guild = channel.guild
                member = await self.get_member_by_id(guild, ban.user_id)
            except Exception:
                self.logger.exception("on_vc_type_changed: could not get member %s", ban.user_id)
                continue

            if action == "CHANGED_FROM_PROTECTED":
                await self._unrestrict(channel, member)
            else:
                await self._restrict(channel, member)

    # ------------------------------------------------------------------ #
    # Periodic expiry check                                                #
    # ------------------------------------------------------------------ #

    @tasks.loop(minutes=5)
    async def check_bans(self):
        async for ban in self.VoiceBan.objects.filter(is_expired=True):
            try:
                if await self.VoiceBan.objects.filter(user_id=ban.user_id).acount() > 1:
                    async for old in self.VoiceBan.objects.filter(user_id=ban.user_id).order_by("-expires_at")[1:]:
                        old.comment = "\n".join(filter(None, [old.comment, "Culled duplicate ban"]))
                        await old.asave()
                        await old.adelete()

                # Find a guild to look up the member in
                guild = next(iter(self.client.guilds), None)
                if guild is None:
                    continue
                member = await self.get_member_by_id(guild, ban.user_id)
                await self.unban_user(member, comment="Ban expired — automatic unban")
                self.logger.info("Expired ban auto-removed for %s", member.display_name)
            except Exception:
                self.logger.exception("Error processing ban expiry for %s", ban.pk)

    @check_bans.before_loop
    async def _before_check_bans(self):
        await self.client.wait_until_ready()

    # ------------------------------------------------------------------ #
    # Slash commands                                                       #
    # ------------------------------------------------------------------ #

    @app_commands.command(name="adm_vban", description="Ban a member from all protected VCs for 60 days [admin]")
    @requires_django_perm("disfunction.can_manage_voice_bans")
    async def cmd_voice_ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        success = await self.ban_user(
            member,
            reason=reason,
            comment="Banned via /adm_vban",
            invoker=interaction.user,
        )
        if success:
            await interaction.followup.send(
                f"✅ **{member.mention}** banned from protected VCs for 60 days.",
                ephemeral=True,
                allowed_mentions=self.mention_reply_only,
            )
        else:
            await interaction.followup.send(
                f"🚫 **{member.mention}** is already banned.",
                ephemeral=True,
                allowed_mentions=self.mention_reply_only,
            )

    @app_commands.command(name="adm_unvban", description="Lift a member's protected VC ban [admin]")
    @requires_django_perm("disfunction.can_manage_voice_bans")
    async def cmd_voice_unban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        comment: str = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        success = await self.unban_user(member, comment=comment, invoker=interaction.user)
        if success:
            await interaction.followup.send(
                f"✅ **{member.mention}** unbanned.",
                ephemeral=True,
                allowed_mentions=self.mention_reply_only,
            )
        else:
            await interaction.followup.send(
                f"🚫 **{member.mention}** is not banned.",
                ephemeral=True,
                allowed_mentions=self.mention_reply_only,
            )

    @app_commands.command(name="adm_listvbans", description="List the 25 soonest-expiring active voice bans [admin]")
    @requires_django_perm("disfunction.can_manage_voice_bans")
    async def cmd_list_voice_bans(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        embed = discord.Embed(title="Active Voice Bans", color=discord.Color.red())
        found = False

        async for ban in self.VoiceBan.objects.filter(is_expired=False).order_by("expires_at")[:25]:
            found = True
            try:
                user = await self.get_member_by_id(interaction.guild, ban.user_id)
                user_display = user.mention
            except Exception:
                user_display = f"UID {ban.user_id}"

            first_history = await sync_to_async(ban.history.first)()
            invoker_id = first_history.history_user_id if first_history else None
            try:
                invoker = await self.get_member_by_id(interaction.guild, invoker_id)
                invoker_display = invoker.mention
            except Exception:
                invoker_display = f"UID {invoker_id}" if invoker_id else "Unknown"

            embed.add_field(
                name="",
                value=(
                    f"{user_display} | {ban.get_kind_display()} | "
                    f"Expires: {format_dt(ban.expires_at, style='R')} | "
                    f"Invoker: {invoker_display}"
                ),
                inline=False,
            )

        if not found:
            embed.add_field(name="No active voice bans.", value="", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=self.mention_reply_only)


async def setup(client):
    await client.add_cog(VoiceBanCog(client))
