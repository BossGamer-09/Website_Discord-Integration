"""
app/disfunction/cogs/voice_tracker.py

Tracks VoiceSession records and VoiceSessionCheckpoints for crash recovery.
Listens to on_voice_state_update, independent of vc_generator.
"""
import asyncio
import logging
import uuid
from datetime import datetime

import discord
from django.apps import apps
from django.utils import timezone
from discord.ext import commands, tasks


class VoiceTrackerCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.VoiceSession = apps.get_model("disfunction", "VoiceSession")
        self.Checkpoint = apps.get_model("disfunction", "VoiceSessionCheckpoint")

        # user_id → checkpoint_id of in-progress session
        self._active: dict[int, str] = {}

        self.checkpoint_flush.start()

    def cog_unload(self):
        self.checkpoint_flush.cancel()

    async def cog_load(self):
        await self._restore_from_checkpoints()

    # ------------------------------------------------------------------ #
    # Startup recovery                                                     #
    # ------------------------------------------------------------------ #

    async def _restore_from_checkpoints(self):
        """Re-populate _active from DB checkpoints left by a previous run."""
        restored = 0
        async for cp in self.Checkpoint.objects.filter(is_active=True):
            self._active[cp.user_id] = cp.checkpoint_id
            restored += 1
        if restored:
            self.logger.info("Restored %d in-progress voice sessions from checkpoints", restored)

    # ------------------------------------------------------------------ #
    # Voice state events                                                   #
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        joined = after.channel and (
            not before.channel or before.channel.id != after.channel.id
        )
        left = before.channel and (
            not after.channel or after.channel.id != before.channel.id
        )

        if joined:
            await self._start_session(member, after.channel, after)

        if left:
            await self._end_session(member, before.channel, before)
        elif before.channel and after.channel and before.channel == after.channel:
            # State changed within same channel (mute/deafen/stream)
            await self._update_checkpoint(member, after)

    # ------------------------------------------------------------------ #
    # Session start                                                        #
    # ------------------------------------------------------------------ #

    async def _start_session(
        self, member: discord.Member, channel: discord.VoiceChannel, state: discord.VoiceState
    ):
        # End any orphaned session for this user first
        if member.id in self._active:
            old_cp_id = self._active.pop(member.id)
            await self.Checkpoint.objects.filter(checkpoint_id=old_cp_id).aupdate(is_active=False)

        cp_id = str(uuid.uuid4())
        self._active[member.id] = cp_id

        try:
            await self.Checkpoint.objects.acreate(
                checkpoint_id=cp_id,
                user_id=member.id,
                guild_id=member.guild.id,
                channel_id=channel.id,
                channel_name=channel.name,
                join_time=timezone.now(),
                is_muted=state.self_mute or state.mute,
                is_deafened=state.self_deaf or state.deaf,
                is_afk=state.afk,
                is_streaming=state.self_stream or False,
                is_active=True,
            )
        except Exception:
            self.logger.exception("Failed to create checkpoint for %s", member.id)

    # ------------------------------------------------------------------ #
    # Session end                                                          #
    # ------------------------------------------------------------------ #

    async def _end_session(
        self, member: discord.Member, channel: discord.VoiceChannel, state: discord.VoiceState
    ):
        cp_id = self._active.pop(member.id, None)

        try:
            if cp_id is None:
                return

            cp = await self.Checkpoint.objects.filter(
                checkpoint_id=cp_id
            ).afirst()

            if cp is None:
                return

            session = self.VoiceSession(
                user_id=member.id,
                guild_id=member.guild.id,
                channel_id=channel.id,
                channel_name=channel.name,
                join_time=cp.join_time,
                leave_time=timezone.now(),
                was_muted=cp.is_muted,
                was_deafened=cp.is_deafened,
                was_afk=cp.is_afk,
                was_streaming=cp.is_streaming,
                talking_time_seconds=cp.accumulated_talking_seconds,
                checkpoint_id=cp.checkpoint_id,
                ended_normally=True,
            )
            await session.asave()

            await self.Checkpoint.objects.filter(checkpoint_id=cp.checkpoint_id).aupdate(is_active=False)

        except Exception:
            self.logger.exception("Failed to finalise session for %s in %s", member.id, channel.id)

    # ------------------------------------------------------------------ #
    # Checkpoint update (in-channel state change)                         #
    # ------------------------------------------------------------------ #

    async def _update_checkpoint(self, member: discord.Member, state: discord.VoiceState):
        cp_id = self._active.get(member.id)
        if not cp_id:
            return
        try:
            await self.Checkpoint.objects.filter(checkpoint_id=cp_id).aupdate(
                is_muted=state.self_mute or state.mute,
                is_deafened=state.self_deaf or state.deaf,
                is_afk=state.afk,
                is_streaming=state.self_stream or False,
            )
        except Exception:
            self.logger.exception("Failed to update checkpoint for %s", member.id)

    # ------------------------------------------------------------------ #
    # Periodic checkpoint flush (accumulate talking time)                  #
    # ------------------------------------------------------------------ #

    @tasks.loop(minutes=2)
    async def checkpoint_flush(self):
        """
        Periodically increments accumulated_talking_seconds for all
        non-muted, non-deafened, non-afk active sessions.
        """
        INCREMENT = 120  # seconds per flush cycle
        try:
            from django.db.models import F
            await (
                self.Checkpoint.objects
                .filter(is_active=True, is_muted=False, is_deafened=False, is_afk=False)
                .aupdate(accumulated_talking_seconds=F("accumulated_talking_seconds") + INCREMENT)
            )
        except Exception:
            self.logger.exception("checkpoint_flush error")

    @checkpoint_flush.before_loop
    async def _before_flush(self):
        await self.client.wait_until_ready()


async def setup(client):
    await client.add_cog(VoiceTrackerCog(client))
