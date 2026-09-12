"""
app/disfunction/cogs/attendance.py

DB-backed event attendance tracking.
Replaces the in-memory VCAttendanceTracker from the old vc_generator.

Persistence: EventAttendance + EventAttendanceRecord rows survive bot restarts.
"""
import asyncio
import logging
import uuid
from datetime import datetime

import discord
from django.apps import apps
from django.utils import timezone
from discord import app_commands
from discord.ext import commands

from app.main.util.discord_command_checks import requires_django_perm
from app.preferences.utils import aget_global_preference
from app.disfunction.preferences import AttendanceLogChannelID


class AttendanceControlView(discord.ui.View):
    def __init__(self, cog, event_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.event_id = event_id

    @discord.ui.button(label="Check-in", style=discord.ButtonStyle.success, emoji="✅")
    async def check_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.check_in_user(interaction, self.event_id)

    @discord.ui.button(label="Randomize Teams", style=discord.ButtonStyle.primary, emoji="🔀")
    async def randomize_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.randomize_teams(interaction, self.event_id)

    @discord.ui.button(label="Stop Tracking", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_tracking(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.stop_event(interaction, self.event_id)


class AttendanceCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.EventAttendance = apps.get_model("disfunction", "EventAttendance")
        self.EventAttendanceRecord = apps.get_model("disfunction", "EventAttendanceRecord")

    # ------------------------------------------------------------------ #
    # Voice state hook — auto join/leave records                          #
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        # Record joins and leaves for all active events tied to VC channels
        joined = after.channel and (not before.channel or before.channel != after.channel)
        left = before.channel and (not after.channel or after.channel != before.channel)

        if joined and after.channel:
            await self._record_join(member, after.channel.id, after)

        if left and before.channel:
            await self._record_leave(member, before.channel.id)

    async def _record_join(self, member: discord.Member, channel_id: int, state: discord.VoiceState):
        try:
            event = await self.EventAttendance.objects.filter(
                voice_channel_id=channel_id, actual_end__isnull=True
            ).afirst()
            if not event:
                return

            await self.EventAttendanceRecord.objects.aupdate_or_create(
                event=event,
                user_id=member.id,
                # join_time is only set on creation — rejoins must not overwrite the
                # original join time or the "late" calculation will use the wrong timestamp.
                create_defaults={"join_time": timezone.now()},
                defaults={
                    "status": "ACTIVE",
                    "leave_time": None,
                    "was_muted": state.self_mute or state.mute,
                    "was_deafened": state.self_deaf or state.deaf,
                    "was_afk": state.afk,
                },
            )
        except Exception:
            self.logger.exception("_record_join error for %s in %s", member.id, channel_id)

    async def _record_leave(self, member: discord.Member, channel_id: int):
        try:
            event = await self.EventAttendance.objects.filter(
                voice_channel_id=channel_id, actual_end__isnull=True
            ).afirst()
            if not event:
                return

            record = await self.EventAttendanceRecord.objects.filter(
                event=event, user_id=member.id, leave_time__isnull=True
            ).afirst()
            if not record:
                return

            record.leave_time = timezone.now()
            record.status = "PRESENT"
            await record.asave()
        except Exception:
            self.logger.exception("_record_leave error for %s in %s", member.id, channel_id)

    # ------------------------------------------------------------------ #
    # Slash commands — start / stop / check-in                           #
    # ------------------------------------------------------------------ #

    @app_commands.command(name="attendance_start", description="Start attendance tracking for your current VC")
    @requires_django_perm("disfunction.add_eventattendance")
    async def cmd_start(
        self,
        interaction: discord.Interaction,
        event_name: str,
        event_type: str = "ORGANIC",
    ):
        await interaction.response.defer(ephemeral=False)

        if not interaction.user.voice:
            await interaction.followup.send("❌ Join a voice channel first.", ephemeral=True)
            return

        vc = interaction.user.voice.channel
        event_id = str(uuid.uuid4())

        event = await self.EventAttendance.objects.acreate(
            event_id=event_id,
            event_name=event_name,
            event_type=event_type.upper(),
            organizer_id=interaction.user.id,
            voice_channel_id=vc.id,
            actual_start=timezone.now(),
        )

        # Seed with current VC members
        for m in vc.members:
            if m.bot:
                continue
            await self.EventAttendanceRecord.objects.acreate(
                event=event,
                user_id=m.id,
                status="ACTIVE",
                join_time=timezone.now(),
                was_muted=m.voice.self_mute or m.voice.mute if m.voice else False,
                was_deafened=m.voice.self_deaf or m.voice.deaf if m.voice else False,
                was_afk=m.voice.afk if m.voice else False,
            )

        seeded = [m for m in vc.members if not m.bot]
        seeded_list = " ".join(m.mention for m in seeded) if seeded else "—"

        embed = discord.Embed(
            title="📊 Attendance Tracking Started",
            color=discord.Color.green(),
            timestamp=timezone.now(),
        )
        embed.add_field(name="Event", value=event_name, inline=True)
        embed.add_field(name="VC", value=vc.mention, inline=True)
        embed.add_field(name="Started by", value=interaction.user.mention, inline=True)
        embed.add_field(name=f"Members seeded ({len(seeded)})", value=seeded_list, inline=False)
        embed.set_footer(text=f"Event ID: {event_id[:8]}…")

        view = AttendanceControlView(self, event_id)
        await interaction.followup.send(embed=embed, view=view)

    async def check_in_user(self, interaction: discord.Interaction, event_id: str):
        try:
            event = await self.EventAttendance.objects.aget(event_id=event_id, actual_end__isnull=True)
        except self.EventAttendance.DoesNotExist:
            await interaction.followup.send("❌ Event not found or already ended.", ephemeral=True)
            return

        record, created = await self.EventAttendanceRecord.objects.aupdate_or_create(
            event=event,
            user_id=interaction.user.id,
            defaults={"status": "PRESENT", "join_time": timezone.now()},
        )
        if created:
            await interaction.followup.send("✅ Checked in!", ephemeral=True)
        else:
            await interaction.followup.send("ℹ️ Already tracked — status updated to Present.", ephemeral=True)

    async def randomize_teams(self, interaction: discord.Interaction, event_id: str):
        try:
            event = await self.EventAttendance.objects.aget(event_id=event_id, actual_end__isnull=True)
        except self.EventAttendance.DoesNotExist:
            await interaction.followup.send("❌ Event not found.", ephemeral=True)
            return

        if event.organizer_id != interaction.user.id and not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("❌ Only the organizer can randomize teams.", ephemeral=True)
            return

        import random
        records = [r async for r in self.EventAttendanceRecord.objects.filter(event=event)]
        random.shuffle(records)

        team_a, team_b = [], []
        for i, record in enumerate(records):
            (team_a if i % 2 == 0 else team_b).append(f"<@{record.user_id}>")

        embed = discord.Embed(title="🔀 Teams Randomized", color=discord.Color.blurple())
        embed.add_field(name="Team A", value="\n".join(team_a) or "—", inline=True)
        embed.add_field(name="Team B", value="\n".join(team_b) or "—", inline=True)
        await interaction.followup.send(embed=embed)

    async def stop_event(self, interaction: discord.Interaction, event_id: str):
        try:
            event = await self.EventAttendance.objects.aget(event_id=event_id, actual_end__isnull=True)
        except self.EventAttendance.DoesNotExist:
            await interaction.followup.send("❌ Event not found or already ended.", ephemeral=True)
            return

        if event.organizer_id != interaction.user.id and not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("❌ Only the organizer can stop tracking.", ephemeral=True)
            return

        now = timezone.now()
        event.actual_end = now
        await event.asave()

        # Close any open records
        async for record in self.EventAttendanceRecord.objects.filter(event=event, leave_time__isnull=True):
            record.leave_time = now
            record.status = "PRESENT"
            await record.asave()

        records = [r async for r in self.EventAttendanceRecord.objects.filter(event=event).order_by("join_time")]
        total = len(records)
        duration = int((now - event.actual_start).total_seconds())
        h, m = duration // 3600, (duration % 3600) // 60

        present   = [r for r in records if r.status in ("PRESENT", "ACTIVE")]
        late      = [r for r in records if r.status == "LATE"]
        left_early = [r for r in records if r.status == "LEFT_EARLY"]
        absent    = [r for r in records if r.status in ("ABSENT", "EXCUSED")]

        def _mentions(rec_list, cap=20):
            lines = [f"<@{r.user_id}>" for r in rec_list[:cap]]
            if len(rec_list) > cap:
                lines.append(f"*+{len(rec_list) - cap} more*")
            return " ".join(lines) if lines else "—"

        log_chan_id_str = await aget_global_preference(AttendanceLogChannelID.import_path)
        embed = discord.Embed(
            title="📊 Attendance Summary",
            color=discord.Color.blue(),
            timestamp=timezone.now(),
        )
        embed.add_field(name="Event",      value=event.event_name,         inline=True)
        embed.add_field(name="Organizer",  value=f"<@{event.organizer_id}>", inline=True)
        embed.add_field(name="Duration",   value=f"{h}h {m}m",             inline=True)
        embed.add_field(name="✅ Present",  value=str(len(present)),         inline=True)
        embed.add_field(name="🕐 Late",     value=str(len(late)),            inline=True)
        embed.add_field(name="🚫 No-show",  value=str(len(absent)),          inline=True)
        if present:
            embed.add_field(name=f"✅ Checked In ({len(present)})", value=_mentions(present), inline=False)
        if late:
            embed.add_field(name=f"🕐 Late Arrivals ({len(late)})", value=_mentions(late), inline=False)
        if left_early:
            embed.add_field(name=f"🏃 Left Early ({len(left_early)})", value=_mentions(left_early), inline=False)
        if absent:
            embed.add_field(name=f"🚫 No-show ({len(absent)})", value=_mentions(absent), inline=False)
        embed.set_footer(text=f"Event ID: {event_id[:8]}…")

        try:
            log_chan = self.client.get_channel(int(log_chan_id_str))
            if log_chan:
                await log_chan.send(embed=embed)
        except Exception:
            self.logger.exception("Could not post attendance summary to log channel")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="attendance_status", description="Show current attendance for your VC's active event")
    @requires_django_perm("disfunction.add_eventattendance")
    async def cmd_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.voice:
            await interaction.followup.send("❌ Join a voice channel first.", ephemeral=True)
            return

        vc = interaction.user.voice.channel
        event = await self.EventAttendance.objects.filter(
            voice_channel_id=vc.id, actual_end__isnull=True
        ).afirst()

        if not event:
            await interaction.followup.send("❌ No active attendance event in your VC.", ephemeral=True)
            return

        count = await self.EventAttendanceRecord.objects.filter(event=event).acount()
        embed = discord.Embed(title=f"📊 {event.event_name}", color=discord.Color.green())
        embed.add_field(name="Type",         value=event.event_type,   inline=True)
        embed.add_field(name="Participants", value=str(count),          inline=True)
        embed.add_field(name="Started",      value=f"<t:{int(event.actual_start.timestamp())}:R>", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(client):
    await client.add_cog(AttendanceCog(client))
