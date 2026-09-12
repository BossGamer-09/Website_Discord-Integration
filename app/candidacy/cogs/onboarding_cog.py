"""
app/candidacy/cogs/onboarding_cog.py

Two responsibilities:
 1. Listen for ONBOARDING_LEADERS_ASSIGNED Redis events → post to OnboardingLeaderChannelID.
 2. Listen for VC join/leave events → if user is an active applicant, notify Chamberlain channel.
"""
import json
import logging

import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)

CANDIDACY_PUBSUB = "app.candidacy.notify"


class OnboardingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self.redis_client = None
        self.pubsub       = None

    async def cog_load(self):
        from django.conf import settings
        import redis.asyncio as redis

        pool              = redis.ConnectionPool.from_url(settings.CACHES["default"]["LOCATION"])
        self.redis_client = redis.Redis.from_pool(pool)
        self.pubsub       = self.redis_client.pubsub()
        await self.pubsub.subscribe(CANDIDACY_PUBSUB)
        self.listener_loop.start()
        log.info("OnboardingCog loaded.")

    async def cog_unload(self):
        self.listener_loop.cancel()
        try:
            await self.pubsub.unsubscribe()
            await self.redis_client.aclose()
        except Exception:
            pass

    @tasks.loop(seconds=0.3)
    async def listener_loop(self):
        try:
            msg = await self.pubsub.get_message(ignore_subscribe_messages=True)
            if msg:
                data   = json.loads(msg["data"])
                action = data.get("action")
                if action == "ONBOARDING_LEADERS_ASSIGNED":
                    await self._on_leaders_assigned(data)
                elif action == "FOLLOWUP_DM":
                    await self._on_followup_dm(data)
                elif action == "APPLICATION_AUTO_CLOSED":
                    await self._on_auto_closed(data)
        except Exception:
            log.exception("OnboardingCog listener error:")

    async def _on_leaders_assigned(self, data: dict):
        from app.preferences.utils import aget_global_preference
        from app.candidacy.preferences import OnboardingLeaderChannelID

        chan_id = await aget_global_preference(OnboardingLeaderChannelID.import_path)
        if not chan_id:
            return

        channel = await self._get_channel(int(chan_id))
        if not channel:
            return

        discord_ids  = data.get("discord_ids", [])
        leader_names = data.get("leader_names", [])
        week_start   = data.get("week_start", "")

        mentions = " ".join(f"<@{uid}>" for uid in discord_ids if uid)
        if not mentions:
            mentions = ", ".join(leader_names) or "No leaders found"

        embed = discord.Embed(
            title       = "🗓️ Weekly Onboarding Leaders",
            description = (
                f"The following leaders are assigned to onboarding for the week starting **{week_start}**:\n\n"
                f"{mentions}\n\n"
                "Please ensure new members receive a proper welcome and orientation."
            ),
            color = 0x5865F2,
        )
        embed.set_footer(text="Blightveil Onboarding • Auto-rotation")

        await channel.send(embed=embed)
        log.info("Onboarding assignment posted for w/c %s", week_start)

    async def _on_followup_dm(self, data: dict):
        discord_id = data.get("discord_id")
        message    = data.get("message", "")
        if not discord_id or not message:
            return
        try:
            user = await self.bot.fetch_user(int(discord_id))
            await user.send(message)
            log.info("Follow-up DM sent to %s (record %s)", discord_id, data.get("record_pk"))
        except discord.Forbidden:
            log.warning("Follow-up DM blocked by %s", discord_id)
        except Exception:
            log.exception("_on_followup_dm failed discord_id=%s", discord_id)

    async def _on_auto_closed(self, data: dict):
        """Lock + archive the application thread for an auto-denied application."""
        thread_id = data.get("thread_id")
        reason    = data.get("reason", "Auto-closed due to inactivity.")
        if not thread_id:
            return
        try:
            thread = await self.bot.fetch_channel(int(thread_id))
            if not isinstance(thread, discord.Thread):
                return
            await thread.send(
                f"⏰ **Application Auto-Closed**\n{reason}\n"
                "This application has been denied due to inactivity and the thread has been archived."
            )
            await thread.edit(archived=True, locked=True)
            log.info("Auto-closed thread %s (record %s)", thread_id, data.get("record_pk"))
        except discord.NotFound:
            log.warning("Thread %s not found for auto-close", thread_id)
        except Exception:
            log.exception("_on_auto_closed failed thread_id=%s", thread_id)

    # ------------------------------------------------------------------ #
    # Applicant VC event tracking
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Notify Chamberlains when an active applicant joins or leaves an event VC."""
        from app.preferences.utils import aget_global_preference
        from app.candidacy.preferences import (
            ChamberlainApplicantEventTrackingEnabled,
            ChamberlainNotifyChannelID,
        )

        if member.bot:
            return

        joined = before.channel is None and after.channel is not None
        left   = before.channel is not None and after.channel is None
        if not joined and not left:
            return

        if not await aget_global_preference(ChamberlainApplicantEventTrackingEnabled.import_path):
            return

        # Only care about active guild scheduled events
        vc = after.channel if joined else before.channel
        guild = member.guild
        active_event_vc_ids = {
            e.channel_id for e in guild.scheduled_events
            if e.status == discord.EventStatus.active and e.channel_id
        }
        if vc.id not in active_event_vc_ids:
            return

        # Check if this Discord user is an active applicant
        is_applicant = await self._is_active_applicant(member.id)
        if not is_applicant:
            return

        chan_id = await aget_global_preference(ChamberlainNotifyChannelID.import_path)
        if not chan_id:
            return

        channel = await self._get_channel(int(chan_id))
        if not channel:
            return

        verb  = "joined" if joined else "left"
        icon  = "🟢" if joined else "🔴"
        event = next(
            (e for e in guild.scheduled_events if e.channel_id == vc.id and e.status == discord.EventStatus.active),
            None,
        )
        event_name = event.name if event else vc.name

        embed = discord.Embed(
            title       = f"{icon} Applicant {verb.title()} Event",
            description = (
                f"{member.mention} ({member.display_name}) **{verb}** "
                f"the event **{event_name}**."
            ),
            color = 0x22C55E if joined else 0xEF4444,
        )
        embed.set_footer(text="Blightveil Candidacy • Applicant Tracking")

        await channel.send(embed=embed)

    @staticmethod
    async def _is_active_applicant(discord_uid: int) -> bool:
        from app.candidacy.models import MembershipApplicationRecord

        return await MembershipApplicationRecord.objects.filter(
            applicant__discorduser__discorduid=discord_uid,
            status=MembershipApplicationRecord.Status.PENDING,
        ).aexists()

    async def _get_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                return None
        return channel


async def setup(bot: commands.Bot):
    await bot.add_cog(OnboardingCog(bot))
