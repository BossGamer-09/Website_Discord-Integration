"""
app/schedevents/cogs/event_lifecycle.py

Handles all event automation:

  Discord native event sync
    on_scheduled_event_user_add    → create/update RSVP as GOING
    on_scheduled_event_user_remove → update RSVP to NOT_GOING
    on_scheduled_event_update      → status=ACTIVE → fire _start_event()

  Redis Pub/Sub IPC (channel: schedevents.sync)
    EventPlanStatusChanged → PUBLISHED: create Discord event + post announcement
    EventPlanStatusChanged → ACTIVE:    spawn VC + start attendance
    EventPlanStatusChanged → COMPLETED: end attendance + trigger report
    EventPlanStatusChanged → CANCELLED: cancel Discord event + DM RSVPs
    EventPlanUpdated       → refresh announcement embed
    EventRSVPChanged       → update count on announcement embed
    EventReminderDue       → DM GOING/MAYBE users or post to channel
    EventReportReady       → post report embed to staff log

  Periodic tasks (every 60 s)
    • check for PUBLISHED events whose start time is within 5 min → start them
    • poll pub/sub messages

  Requires:  guild_scheduled_events intent enabled in DISCORD_INTENTS.
"""
import asyncio
import json
import logging
from datetime import timedelta

import discord
from asgiref.sync import sync_to_async
from discord.ext import commands, tasks
from django.apps import apps
from django.utils import timezone

from app.preferences.utils import aget_global_preference
from app.schedevents import preferences as prefs
from app.schedevents.cogs.event_manager import build_event_embed
from app.schedevents.cogs.event_rsvp import get_rsvp_view

_build_event_embed = sync_to_async(build_event_embed)

log = logging.getLogger(__name__)

PUBSUB_CHANNEL = "schedevents.sync"


def _mentions_from_role_ids(role_ids) -> tuple[str, discord.AllowedMentions]:
    """Build a `<@&ID> <@&ID>` mention string + AllowedMentions restricted to those roles.

    Returns ("", AllowedMentions.none()) when role_ids is empty, so callers can
    safely concatenate and pass `allowed_mentions` unconditionally.
    """
    ids = [int(r) for r in (role_ids or []) if r]
    if not ids:
        return "", discord.AllowedMentions.none()
    content = " ".join(f"<@&{rid}>" for rid in ids)
    return content, discord.AllowedMentions(roles=[discord.Object(id=rid) for rid in ids])


class EventLifecycleCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)

        self.EventPlan = apps.get_model("schedevents", "EventPlan")
        self.EventRSVP = apps.get_model("schedevents", "EventRSVP")
        self.EventReminder = apps.get_model("schedevents", "EventReminder")
        self.EventReport = apps.get_model("schedevents", "EventReport")

        # disfunction attendance models
        self.EventAttendance = apps.get_model("disfunction", "EventAttendance")
        self.EventAttendanceRecord = apps.get_model("disfunction", "EventAttendanceRecord")

        self._pubsub = None
        self.startup_task.start()

    def cog_unload(self):
        self.startup_task.cancel()
        self.poller_loop.cancel()
        self.auto_starter_loop.cancel()
        if self._pubsub:
            asyncio.create_task(self._pubsub.aclose())
        if hasattr(self, '_redis_client') and self._redis_client:
            asyncio.create_task(self._redis_client.aclose())

    # ------------------------------------------------------------------ #
    # Startup
    # ------------------------------------------------------------------ #

    @tasks.loop(count=1)
    async def startup_task(self):
        await self.client.wait_until_ready()
        import redis.asyncio as aioredis
        from django.conf import settings

        redis_url = settings.CACHES['default']['LOCATION']
        pool = aioredis.ConnectionPool.from_url(redis_url)
        self._redis_client = aioredis.Redis.from_pool(pool)
        self._pubsub = self._redis_client.pubsub()
        await self._pubsub.subscribe(PUBSUB_CHANNEL)

        self.poller_loop.start()
        self.auto_starter_loop.start()
        self.logger.info("EventLifecycleCog ready, subscribed to %s", PUBSUB_CHANNEL)

    # ------------------------------------------------------------------ #
    # Pub/Sub polling loop (every second)
    # ------------------------------------------------------------------ #

    @tasks.loop(seconds=1)
    async def poller_loop(self):
        if not self._pubsub:
            return
        try:
            msg = await self._pubsub.get_message(ignore_subscribe_messages=True)
            if msg and msg.get("data"):
                data = json.loads(msg["data"])
                asyncio.create_task(self._dispatch(data))
        except Exception:
            self.logger.exception("EventLifecycleCog: pubsub poll error")

    async def _dispatch(self, data: dict):
        msg_type = data.get("type")
        try:
            if msg_type == "EventPlanStatusChanged":
                await self._on_status_changed(data)
            elif msg_type == "EventPlanUpdated":
                await self._on_plan_updated(data)
            elif msg_type == "EventRSVPChanged":
                await self._on_rsvp_changed(data)
            elif msg_type == "EventReminderDue":
                await self._on_reminder_due(data)
            elif msg_type == "EventReportReady":
                await self._on_report_ready(data)
            elif msg_type == "EventPlanDeleted":
                await self._on_deleted(data)
            elif msg_type == "WeeklyDigest":
                await self._on_weekly_digest(data)
        except Exception:
            self.logger.exception("EventLifecycleCog._dispatch error for type=%s", msg_type)

    # ------------------------------------------------------------------ #
    # Auto-starter loop (every 60 s) — starts events whose time has come
    # ------------------------------------------------------------------ #

    @tasks.loop(seconds=60)
    async def auto_starter_loop(self):
        now = timezone.now()
        start_window  = now + timedelta(minutes=5)
        unlock_window = now + timedelta(minutes=30)
        vc_window     = now + timedelta(minutes=60)

        # Auto-start: PUBLISHED events whose start time is within 5 min
        async for plan in self.EventPlan.objects.filter(
            status=self.EventPlan.Status.PUBLISHED,
            planned_start_at__lte=vc_window,
        ).select_related("indicator", "vc_profile"):
            if plan.planned_start_at <= start_window:
                self.logger.info("Auto-starting event %s", plan.codename_id)
                await self._start_event(plan)
            elif plan.planned_start_at <= unlock_window:
                # 30-min window: unlock the VC if it exists
                if plan.active_vc_channel_id:
                    await self._unlock_event_vc(plan)
                elif plan.auto_generate_vc:
                    # Published late — missed the 60-min pre-create window; create unlocked now
                    await self._pre_create_event_vc(plan, locked=False)
            else:
                # 60-min window: create locked VC if not yet created
                if plan.auto_generate_vc and not plan.active_vc_channel_id:
                    await self._pre_create_event_vc(plan)

        # Auto-end: ACTIVE events whose planned end time has passed
        # planned_end_at is a property (start + duration), so compute it in the ORM
        from django.db.models import ExpressionWrapper, F, DateTimeField as DTField
        end_expr = ExpressionWrapper(
            F("planned_start_at") + F("planned_duration"),
            output_field=DTField(),
        )
        async for plan in self.EventPlan.objects.filter(
            status=self.EventPlan.Status.ACTIVE,
        ).annotate(computed_end=end_expr).filter(
            computed_end__lte=now,
        ).select_related("indicator", "vc_profile"):
            self.logger.info("Auto-ending event %s (planned_end_at passed)", plan.codename_id)
            plan.status = self.EventPlan.Status.COMPLETED
            plan._skip_signals = True
            await plan.asave(update_fields=["status"])
            await self._end_event(plan)

    # ------------------------------------------------------------------ #
    # Discord native event listeners
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_scheduled_event_user_add(
        self, event: discord.ScheduledEvent, user: discord.User
    ):
        """User clicked 'Interested' on a Discord native event → sync as GOING RSVP."""
        plan = await self._plan_for_discord_event(event.id)
        if not plan:
            return

        org_user = await self._get_org_user(user.id)
        if not org_user:
            return

        await self._upsert_rsvp(plan, org_user, self.EventRSVP.RSVPStatus.GOING, source="discord_interest")
        await self._sync_thread_member(plan.guild_id, plan.attendee_thread_id, user.id, "GOING")

    @commands.Cog.listener()
    async def on_scheduled_event_user_remove(
        self, event: discord.ScheduledEvent, user: discord.User
    ):
        """User un-clicked 'Interested' → sync as NOT_GOING."""
        plan = await self._plan_for_discord_event(event.id)
        if not plan:
            return

        org_user = await self._get_org_user(user.id)
        if not org_user:
            return

        rsvp = await self.EventRSVP.objects.filter(event=plan, user=org_user).afirst()
        if rsvp and rsvp.status == self.EventRSVP.RSVPStatus.GOING:
            rsvp.status = self.EventRSVP.RSVPStatus.NOT_GOING
            await rsvp.asave(update_fields=["status"])
        await self._sync_thread_member(plan.guild_id, plan.attendee_thread_id, user.id, "NOT_GOING")

    @commands.Cog.listener()
    async def on_scheduled_event_update(
        self, before: discord.ScheduledEvent, after: discord.ScheduledEvent
    ):
        """Sync Discord native event status changes back to EventPlan."""
        if before.status == after.status:
            return

        if after.status == discord.EventStatus.active:
            plan = await self._plan_for_discord_event(after.id)
            if plan and plan.status == self.EventPlan.Status.PUBLISHED:
                await self._start_event(plan)

        elif after.status == discord.EventStatus.ended:
            plan = await self._plan_for_discord_event(after.id)
            if plan and plan.status == self.EventPlan.Status.ACTIVE:
                plan.status = self.EventPlan.Status.COMPLETED
                plan._skip_signals = True
                await plan.asave(update_fields=["status"])
                await self._end_event(plan)

        elif after.status == discord.EventStatus.cancelled:
            plan = await self._plan_for_discord_event(after.id)
            if not plan:
                return
            if plan.status in (self.EventPlan.Status.PUBLISHED, self.EventPlan.Status.ACTIVE):
                plan.status = self.EventPlan.Status.CANCELLED
                plan._skip_signals = True
                await plan.asave(update_fields=["status"])
                await self._on_cancelled(plan)

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent):
        """Discord event deleted directly — cancel & clean up our EventPlan."""
        plan = await self._plan_for_discord_event(event.id)
        if not plan:
            return
        if plan.status not in (
            self.EventPlan.Status.PUBLISHED,
            self.EventPlan.Status.ACTIVE,
        ):
            return
        plan.status = self.EventPlan.Status.CANCELLED
        plan._skip_signals = True
        await plan.asave(update_fields=["status"])
        await self._on_cancelled(plan)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """User joins the event's VC → auto check-in their RSVP."""
        if not after.channel:
            return

        auto_checkin = await aget_global_preference(prefs.EventAutoCheckinEnabled.import_path)
        if not auto_checkin:
            return

        plan = await self.EventPlan.objects.filter(
            active_vc_channel_id=after.channel.id,
            status=self.EventPlan.Status.ACTIVE,
        ).afirst()
        if not plan:
            return

        org_user = await self._get_org_user(member.id)
        if not org_user:
            return

        rsvp = await self.EventRSVP.objects.filter(
            event=plan, user=org_user
        ).afirst()

        now = timezone.now()
        late_threshold = await aget_global_preference(prefs.EventLateThresholdMinutes.import_path)
        late_cutoff = plan.planned_start_at + timedelta(minutes=int(late_threshold or 10))
        is_late = now > late_cutoff

        if rsvp:
            if not rsvp.checked_in:
                rsvp.checked_in = True
                rsvp.check_in_time = now
                rsvp.check_in_source = self.EventRSVP.CheckInSource.VC_JOIN
                if is_late and rsvp.attendance_outcome is None:
                    rsvp.attendance_outcome = self.EventRSVP.AttendanceOutcome.LATE
                await rsvp.asave(update_fields=[
                    "checked_in", "check_in_time", "check_in_source", "attendance_outcome"
                ])
        else:
            # Walk-in (not RSVP'd) — create GOING + checked-in record
            await self.EventRSVP.objects.acreate(
                event=plan,
                user=org_user,
                status=self.EventRSVP.RSVPStatus.GOING,
                checked_in=True,
                check_in_time=now,
                check_in_source=self.EventRSVP.CheckInSource.VC_JOIN,
                attendance_outcome=self.EventRSVP.AttendanceOutcome.LATE if is_late else None,
            )

    # ------------------------------------------------------------------ #
    # Status change handler (from Redis pub/sub)
    # ------------------------------------------------------------------ #

    async def _on_status_changed(self, data: dict):
        codename_id = data["codename_id"]
        status = data["status"]
        guild_id = data["guild_id"]

        plan = await self.EventPlan.objects.select_related("indicator", "vc_profile").filter(
            codename_id=codename_id
        ).afirst()
        if not plan:
            return

        if status == self.EventPlan.Status.PUBLISHED:
            await self._on_published(plan)

        elif status == self.EventPlan.Status.ACTIVE:
            await self._start_event(plan)

        elif status == self.EventPlan.Status.COMPLETED:
            await self._end_event(plan)

        elif status == self.EventPlan.Status.CANCELLED:
            await self._on_cancelled(plan)

    async def _on_plan_updated(self, data: dict):
        """Refresh announcement embed when non-status fields change."""
        plan = await self.EventPlan.objects.select_related("indicator").filter(
            codename_id=data["codename_id"]
        ).afirst()
        if not plan or not plan.announcement_message_id or not plan.announcement_channel_id:
            return
        await self._refresh_announcement_embed(plan)

    async def _on_rsvp_changed(self, data: dict):
        """Update announcement embed and add/remove user from attendee thread."""
        plan = await self.EventPlan.objects.select_related("indicator").filter(
            codename_id=data["codename_id"]
        ).afirst()
        if not plan:
            return

        await self._refresh_announcement_embed(plan)
        await self._sync_thread_member(
            guild_id=data.get("guild_id"),
            thread_id=data.get("attendee_thread_id") or plan.attendee_thread_id,
            discord_uid=data.get("discord_uid"),
            status=data.get("status"),
        )

    async def _on_reminder_due(self, data: dict):
        codename_id = data["codename_id"]
        target = data["target"]
        channel_id = data["channel_id"]
        minutes_before = data["minutes_before"]
        custom_message = data.get("custom_message")

        plan = await self.EventPlan.objects.select_related("indicator").filter(
            codename_id=codename_id
        ).afirst()
        if not plan:
            return

        ts = int(plan.planned_start_at.timestamp())
        default_msg = (
            f"⏰ **Reminder:** {plan.indicator.prefix} **{plan.title}** starts "
            f"<t:{ts}:R> (<t:{ts}:F>).\n"
            f"React to get ready! Event ID: `{plan.codename_id}`"
        )
        content = custom_message or default_msg

        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            return

        if target == "CHANNEL":
            ch_id = channel_id or await aget_global_preference(prefs.EventReminderFallbackChannelID.import_path)
            if ch_id:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    await ch.send(content)
            return

        # DM all GOING and MAYBE RSVPs
        rsvps = self.EventRSVP.objects.filter(
            event=plan,
            status__in=[self.EventRSVP.RSVPStatus.GOING, self.EventRSVP.RSVPStatus.MAYBE],
        ).select_related("user__discorduser")
        fallback_ch_id = await aget_global_preference(prefs.EventReminderFallbackChannelID.import_path)
        fallback_ch = guild.get_channel(int(fallback_ch_id)) if fallback_ch_id else None

        async for rsvp in rsvps:
            discord_id = getattr(getattr(rsvp.user, "discorduser", None), "discorduid", None)
            member = guild.get_member(discord_id) if discord_id else None
            if not member:
                continue
            try:
                await member.send(content)
            except discord.Forbidden:
                if fallback_ch:
                    await fallback_ch.send(f"{member.mention} {content}")

    async def _on_report_ready(self, data: dict):
        """Post post-event summary embeds to staff log channel.

        Handles Discord limits automatically:
          • 25 fields per embed
          • 6000 total characters per embed
          • 1024 characters per field value
        Large reports spill into continuation embeds sent in the same message batch.
        """
        plan = await self.EventPlan.objects.select_related("indicator").filter(
            codename_id=data["codename_id"]
        ).afirst()
        if not plan:
            return

        staff_log_id = await aget_global_preference(prefs.EventStaffLogChannelID.import_path)
        if not staff_log_id:
            self.logger.warning("_on_report_ready: EventStaffLogChannelID preference not set — report not posted for %s", data.get("codename_id"))
            return

        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            self.logger.warning("_on_report_ready: guild %s not found", plan.guild_id)
            return

        ch = guild.get_channel(int(staff_log_id))
        if not ch:
            self.logger.warning("_on_report_ready: staff log channel %s not found in guild", staff_log_id)
            return

        color = plan.indicator.discord_color
        footer_text = f"Report generated {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}"

        # ------------------------------------------------------------------ #
        # Helpers
        # ------------------------------------------------------------------ #

        def _fmt_dur(seconds: int) -> str:
            if seconds <= 0:
                return "—"
            h, rem = divmod(seconds, 3600)
            m = rem // 60
            return f"{h}h {m}m" if h else f"{m}m"

        def _user_mention(e: dict) -> str:
            return f"<@{e['uid']}>" if e.get("uid") else e.get("name", "?")

        def _vc_line(r: dict) -> str:
            dur = _fmt_dur(r["duration_s"])
            tags = []
            if r["joined_late_min"] > 0:
                tags.append(f"joined {r['joined_late_min']}m late")
            if r["left_early_min"] > 0:
                tags.append(f"left {r['left_early_min']}m early")
            if r.get("was_muted") and r.get("was_deafened"):
                tags.append("deaf")
            suffix = f"  *({', '.join(tags)})*" if tags else ""
            return f"<@{r['uid']}>  `{dur}`{suffix}"

        # ------------------------------------------------------------------ #
        # EmbedBuilder — auto-paginates across Discord limits
        # ------------------------------------------------------------------ #

        FIELD_VALUE_LIMIT = 1024
        FIELDS_PER_EMBED  = 25
        EMBED_CHAR_LIMIT  = 6000

        class EmbedBuilder:
            def __init__(self, title: str, color: int, footer: str):
                self._color   = color
                self._footer  = footer
                self._embeds: list[discord.Embed] = []
                self._current = discord.Embed(title=title, color=color)
                self._char_count = len(title)

            def _field_chars(self, name: str, value: str) -> int:
                return len(name) + len(value)

            def _current_fields(self) -> int:
                return len(self._current.fields)

            def _spill(self):
                """Seal the current embed and start a continuation."""
                self._current.set_footer(text=self._footer)
                self._embeds.append(self._current)
                self._current = discord.Embed(color=self._color)
                self._char_count = 0

            def add_field(self, name: str, value: str, inline: bool = False):
                if not value:
                    value = "\u200b"
                # Truncate value to field limit
                if len(value) > FIELD_VALUE_LIMIT:
                    value = value[: FIELD_VALUE_LIMIT - 3] + "…"
                fc = self._field_chars(name, value)
                if (
                    self._current_fields() >= FIELDS_PER_EMBED
                    or self._char_count + fc > EMBED_CHAR_LIMIT
                ):
                    self._spill()
                self._current.add_field(name=name, value=value, inline=inline)
                self._char_count += fc

            def add_list_field(self, name: str, lines: list[str], inline: bool = False):
                """
                Add a field whose value is a newline-joined list.
                If the full list doesn't fit in one field value, chunks it across
                multiple fields using "name (cont.)" headers.
                """
                if not lines:
                    self.add_field(name, "*None*", inline=inline)
                    return

                chunk: list[str] = []
                used = 0
                overflow_suffix = 30  # chars reserved for "…and N more"
                first = True

                for i, line in enumerate(lines):
                    line_len = len(line) + 1  # +1 for \n
                    remaining = len(lines) - i
                    fits = used + line_len + (overflow_suffix if remaining > 1 else 0) <= FIELD_VALUE_LIMIT

                    if not fits:
                        # Flush current chunk
                        value = "\n".join(chunk)
                        field_name = name if first else f"{name} (cont.)"
                        self.add_field(field_name, value, inline=inline)
                        first = False
                        chunk = []
                        used = 0

                    chunk.append(line)
                    used += line_len

                if chunk:
                    field_name = name if first else f"{name} (cont.)"
                    self.add_field(field_name, "\n".join(chunk), inline=inline)

            def build(self) -> list[discord.Embed]:
                if self._current.fields:
                    self._current.set_footer(text=self._footer)
                    self._embeds.append(self._current)
                elif not self._embeds:
                    # Degenerate case: no fields at all
                    self._current.set_footer(text=self._footer)
                    self._embeds.append(self._current)
                return self._embeds

        # ------------------------------------------------------------------ #
        # Build sections
        # ------------------------------------------------------------------ #

        n_going   = data.get("total_rsvp_going", 0)
        n_in      = data.get("total_checked_in", 0)
        n_late    = data.get("total_late", 0)
        n_no_show = data.get("total_no_show", 0)

        builder = EmbedBuilder(
            title=f"📊 Event Report — {plan.indicator.prefix} {plan.title}",
            color=color,
            footer=footer_text,
        )

        # Summary stats row
        builder.add_field("Event ID",       f"`{plan.codename_id}`", inline=True)
        builder.add_field("✅ RSVP'd Going", str(n_going),            inline=True)
        builder.add_field("\u200b",          "\u200b",                inline=True)
        builder.add_field("🟢 On Time",      str(n_in),               inline=True)
        builder.add_field("🕐 Late",         str(n_late),             inline=True)
        builder.add_field("❌ No-show",      str(n_no_show),          inline=True)

        # RSVP attendance sections
        users_in      = data.get("users_checked_in", [])
        users_late    = data.get("users_late", [])
        users_no_show = data.get("users_no_show", [])

        builder.add_list_field(
            f"🟢 Checked In ({n_in})",
            [_user_mention(e) for e in users_in] if users_in else [],
        )
        if users_late:
            builder.add_list_field(
                f"🕐 Late ({n_late})",
                [_user_mention(e) for e in users_late],
            )
        if users_no_show:
            builder.add_list_field(
                f"❌ No-show ({n_no_show})",
                [_user_mention(e) for e in users_no_show],
            )

        # VC sections
        vc_records: list[dict] = data.get("vc_records", [])
        if vc_records:
            late_uids        = {r["uid"] for r in vc_records if r["joined_late_min"] > 0 or r["status"] == "LATE"}
            left_early_uids  = {r["uid"] for r in vc_records if r["left_early_min"] > 0 or r["status"] == "LEFT_EARLY"}

            vc_present_only = [
                r for r in vc_records
                if r["status"] in ("PRESENT", "ACTIVE")
                and r["uid"] not in late_uids
                and r["uid"] not in left_early_uids
            ]
            vc_late       = [r for r in vc_records if r["uid"] in late_uids]
            vc_left_early = [r for r in vc_records if r["uid"] in left_early_uids and r["uid"] not in late_uids]
            vc_absent     = [r for r in vc_records if r["status"] == "ABSENT"]

            if vc_present_only:
                builder.add_list_field(
                    f"🎙️ VC — Present ({len(vc_present_only)})",
                    [_vc_line(r) for r in vc_present_only],
                )
            if vc_late:
                builder.add_list_field(
                    f"🕐 VC — Joined Late ({len(vc_late)})",
                    [_vc_line(r) for r in vc_late],
                )
            if vc_left_early:
                builder.add_list_field(
                    f"🏃 VC — Left Early ({len(vc_left_early)})",
                    [_vc_line(r) for r in vc_left_early],
                )
            if vc_absent:
                builder.add_list_field(
                    f"🚫 VC — No-show ({len(vc_absent)})",
                    [f"<@{r['uid']}>" for r in vc_absent],
                )

        # ------------------------------------------------------------------ #
        # Send — Discord allows max 10 embeds per message; chunk if needed
        # ------------------------------------------------------------------ #
        embeds = builder.build()
        for i in range(0, len(embeds), 10):
            await ch.send(embeds=embeds[i : i + 10])

        # --- Public summary to announcement channel ---
        post_public = await aget_global_preference(prefs.EventPostSummaryToAnnouncementChannel.import_path)
        if post_public and plan.announcement_channel_id:
            ann_ch = guild.get_channel(int(plan.announcement_channel_id))
            if ann_ch:
                ts_start = int(plan.planned_start_at.timestamp())
                duration_str = ""
                if plan.planned_duration:
                    total_mins = int(plan.planned_duration.total_seconds() // 60)
                    h, m = divmod(total_mins, 60)
                    duration_str = f"{h}h {m}m" if h else f"{m}m"

                summary_embed = discord.Embed(
                    title=f"✅ {plan.indicator.prefix} {plan.title} — Completed",
                    color=plan.indicator.discord_color,
                    timestamp=timezone.now(),
                )
                summary_embed.add_field(name="📅 Held",       value=f"<t:{ts_start}:F>",      inline=True)
                if duration_str:
                    summary_embed.add_field(name="⏱️ Duration",  value=duration_str,              inline=True)
                summary_embed.add_field(name="✅ RSVP'd",      value=str(n_going),               inline=True)
                summary_embed.add_field(name="🟢 Attended",    value=str(n_in + n_late),          inline=True)
                summary_embed.add_field(name="❌ No-show",     value=str(n_no_show),              inline=True)
                summary_embed.set_footer(text=f"Event ID: {plan.codename_id}")
                await ann_ch.send(embed=summary_embed)

    async def _on_deleted(self, data: dict):
        """EventPlan was deleted — clean up Discord scheduled event, announcement, VC, thread."""
        guild_id = data.get("guild_id")
        guild = self.client.get_guild(guild_id) if guild_id else None
        if not guild:
            return

        # 1. Cancel Discord native scheduled event
        discord_event_id = data.get("discord_scheduled_event_id")
        if discord_event_id:
            try:
                event = guild.get_scheduled_event(discord_event_id)
                if not event:
                    event = await guild.fetch_scheduled_event(discord_event_id)
                if event and event.status not in (
                    discord.EventStatus.cancelled,
                    discord.EventStatus.ended,
                ):
                    await event.cancel()
            except (discord.NotFound, discord.HTTPException):
                pass

        # 2. Delete announcement message
        ann_ch_id = data.get("announcement_channel_id")
        ann_msg_id = data.get("announcement_message_id")
        if ann_ch_id and ann_msg_id:
            ch = guild.get_channel(ann_ch_id)
            if ch:
                try:
                    msg = await ch.fetch_message(ann_msg_id)
                    await msg.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

        # 3. Lock and archive attendee thread
        thread_id = data.get("attendee_thread_id")
        if thread_id:
            thread = guild.get_thread(thread_id)
            if not thread:
                try:
                    thread = await guild.fetch_channel(thread_id)
                except Exception:
                    thread = None
            if thread:
                try:
                    await thread.send("🗑️ This event has been deleted.")
                    await thread.edit(locked=True, archived=True)
                except (discord.HTTPException, discord.Forbidden):
                    pass

        # 4. Delete event VC
        vc_id = data.get("active_vc_channel_id")
        if vc_id:
            vc = guild.get_channel(vc_id)
            if vc:
                try:
                    await vc.delete(reason="Event deleted")
                except (discord.HTTPException, discord.Forbidden):
                    pass

        self.logger.info("EventPlanDeleted cleanup done for %s", data.get("codename_id"))

    async def _on_weekly_digest(self, data: dict):
        """Post or edit the pinned weekly event digest message."""
        from app.schedevents.preferences import WeeklyDigestMessageID

        channel_id = data.get("channel_id")
        message_id = data.get("message_id") or 0
        content    = data.get("content", "")

        if not channel_id:
            return

        channel = self.client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.client.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                self.logger.warning("WeeklyDigest: channel %s not found", channel_id)
                return

        if message_id:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(content=content)
                self.logger.info("WeeklyDigest: edited message %s", message_id)
                return
            except (discord.NotFound, discord.HTTPException):
                pass  # fall through to post new

        msg = await channel.send(content=content)
        try:
            await msg.pin()
        except (discord.HTTPException, discord.Forbidden):
            pass

        # Persist new message ID so next run edits instead of posting
        from asgiref.sync import sync_to_async
        from app.preferences.models import GlobalSetting

        def _save_msg_id():
            data = GlobalSetting.get_setting_obj(WeeklyDigestMessageID.import_path)
            data.value = msg.id
            data.save()

        await sync_to_async(_save_msg_id)()
        self.logger.info("WeeklyDigest: posted new message %s", msg.id)

    # ------------------------------------------------------------------ #
    # Core lifecycle methods
    # ------------------------------------------------------------------ #

    async def _on_published(self, plan):
        """
        Create a Discord GuildScheduledEvent, post the announcement embed,
        spawn recurring instances (if recurring_rule set).
        """
        if not plan.guild_id:
            self.logger.error(
                "EventLifecycleCog._on_published: plan %s has guild_id=0. "
                "Check that BotGuildID preference is set and the web server was restarted.",
                plan.codename_id,
            )
            return

        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            self.logger.error(
                "EventLifecycleCog._on_published: guild %s not found in cache for plan %s. "
                "Is the bot in the server and are guild intents enabled?",
                plan.guild_id, plan.codename_id,
            )
            return

        # 1. Create Discord native scheduled event (VC will be added 1 hour before start)
        vc_channel = None
        if not plan.discord_scheduled_event_id:
            discord_event = await self._create_discord_scheduled_event(guild, plan, vc_channel=None)
            if discord_event:
                plan.discord_scheduled_event_id = discord_event.id
                await plan.asave(update_fields=["discord_scheduled_event_id"])

        # 3. Post announcement embed (ping mentions_on_create roles)
        ann_chan_id = plan.announcement_channel_override_id or await aget_global_preference(prefs.EventAnnouncementChannelID.import_path)
        if ann_chan_id:
            ch = guild.get_channel(int(ann_chan_id))
            if ch:
                embed = await _build_event_embed(plan)
                view = await get_rsvp_view(plan)
                mention_content, allowed = _mentions_from_role_ids(plan.mentions_on_create)
                msg = await ch.send(
                    content=mention_content or None,
                    embed=embed,
                    view=view,
                    allowed_mentions=allowed,
                )
                plan.announcement_channel_id = ch.id
                plan.announcement_message_id = msg.id
                await plan.asave(update_fields=["announcement_channel_id", "announcement_message_id"])

        # 4. Create attendee thread on the announcement message
        if plan.announcement_message_id and plan.announcement_channel_id:
            ch = guild.get_channel(plan.announcement_channel_id)
            if ch:
                try:
                    ann_msg = await ch.fetch_message(plan.announcement_message_id)

                    # track whether the thread was created from the announcement
                    # message (in which case Discord auto-shows it — no forward needed)
                    thread_from_message = False

                    if plan.private_thread:
                        # Private thread: only explicitly added members can see it.
                        # Requires server boost level 2; falls back to public on failure.
                        try:
                            thread = await ch.create_thread(
                                name=f"🔒 {plan.title} — Attendees",
                                type=discord.ChannelType.private_thread,
                                auto_archive_duration=10080,
                                invitable=False,  # only staff can add members
                                reason=f"Private attendee thread for {plan.codename_id}",
                            )
                            # Pin the announcement message so members can find context
                            try:
                                await ann_msg.pin()
                            except (discord.HTTPException, discord.Forbidden):
                                pass
                        except (discord.HTTPException, discord.Forbidden):
                            self.logger.warning(
                                "Private thread creation failed for %s (boost level?), "
                                "falling back to public thread.", plan.codename_id,
                            )
                            thread = await ann_msg.create_thread(
                                name=f"📋 {plan.title} — Discussion",
                                auto_archive_duration=10080,
                            )
                            thread_from_message = True
                    else:
                        thread = await ann_msg.create_thread(
                            name=f"📋 {plan.title} — Discussion",
                            auto_archive_duration=10080,
                        )
                        thread_from_message = True

                    plan.attendee_thread_id = thread.id
                    await plan.asave(update_fields=["attendee_thread_id"])

                    # Forward embed only for private threads not attached to a message —
                    # message-based threads already show the source embed automatically.
                    if not thread_from_message:
                        try:
                            fwd_embed = await _build_event_embed(plan)
                            await thread.send(embed=fwd_embed)
                        except (discord.HTTPException, discord.Forbidden):
                            pass

                    now_ts = timezone.now().timestamp()
                    ts = int(plan.planned_start_at.timestamp())
                    vc_create_ts = ts - 3600
                    vc_unlock_ts = ts - 1800

                    if vc_create_ts > now_ts:
                        vc_line = f"🔒 A voice channel will be created <t:{vc_create_ts}:R> and unlocked <t:{vc_unlock_ts}:R>."
                    elif vc_unlock_ts > now_ts:
                        vc_line = f"🔒 A voice channel will be unlocked <t:{vc_unlock_ts}:R> (30 min before start)."
                    else:
                        vc_line = f"🔊 A voice channel will be available when the event starts."

                    if plan.private_thread:
                        lines = [
                            f"## 🔒 {plan.indicator.prefix} {plan.title}",
                            f"> This is a **private thread** — you're here because you RSVP'd. Only attendees can see this.",
                            f"",
                            f"📅 Event starts <t:{ts}:F> (<t:{ts}:R>)",
                            vc_line,
                            f"",
                            f"Use this thread to coordinate, ask questions, or share loadouts before the event.",
                        ]
                        await thread.send("\n".join(lines))
                    else:
                        msg = (
                            f"Welcome to the **{plan.title}** event thread!\n"
                            f"RSVP above, then use this thread for coordination.\n"
                            f"Event starts <t:{ts}:R>.\n"
                            f"{vc_line}"
                        )
                        await thread.send(msg)

                    # Seed existing GOING/MAYBE RSVPs into the thread immediately
                    if plan.private_thread:
                        async for rsvp in self.EventRSVP.objects.filter(
                            event=plan,
                            status__in=[
                                self.EventRSVP.RSVPStatus.GOING,
                                self.EventRSVP.RSVPStatus.MAYBE,
                                self.EventRSVP.RSVPStatus.WAITLISTED,
                            ],
                        ).select_related("user__discorduser"):
                            discord_id = getattr(getattr(rsvp.user, "discorduser", None), "discorduid", None)
                            member = guild.get_member(discord_id) if discord_id else None
                            if member:
                                try:
                                    await thread.add_user(member)
                                except (discord.HTTPException, discord.Forbidden):
                                    pass

                except (discord.HTTPException, discord.Forbidden):
                    pass

        # 5. Auto-create default DM reminder if none exist yet and preference is set
        default_reminder_mins = await aget_global_preference(prefs.EventDefaultReminderMinutes.import_path)
        if default_reminder_mins:
            EventReminder = apps.get_model("schedevents", "EventReminder")
            already_exists = await EventReminder.objects.filter(
                event=plan, target="DM"
            ).aexists()
            if not already_exists:
                await EventReminder.objects.acreate(
                    event=plan,
                    minutes_before=int(default_reminder_mins),
                    target="DM",
                )
                self.logger.info(
                    "Auto-created %d-min DM reminder for event %s",
                    default_reminder_mins, plan.codename_id,
                )

        # 6. Spawn recurring instances in background
        if plan.recurring_rule and not plan.parent_event_id:
            from app.schedevents.tasks import spawn_recurring_instances
            spawn_recurring_instances.delay(plan.codename_id)

    async def _start_event(self, plan):
        """
        Transitions PUBLISHED → ACTIVE.
        Spawns the event VC, seeds attendance tracking, notifies attendee thread.
        """
        if plan.status == self.EventPlan.Status.ACTIVE:
            return  # already started

        plan.status = self.EventPlan.Status.ACTIVE
        plan._skip_signals = True
        await plan.asave(update_fields=["status"])

        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            return

        # 1. Create Discord scheduled event if publish missed it
        if not plan.discord_scheduled_event_id:
            discord_event = await self._create_discord_scheduled_event(guild, plan, vc_channel=None)
            if discord_event:
                plan.discord_scheduled_event_id = discord_event.id
                await plan.asave(update_fields=["discord_scheduled_event_id"])

        # 2. Spawn or unlock event VC
        vc_channel = None
        if plan.auto_generate_vc:
            if plan.active_vc_channel_id:
                # VC was pre-created locked — unlock it now
                vc_channel = guild.get_channel(plan.active_vc_channel_id)
                if vc_channel:
                    await self._unlock_event_vc(plan, vc_channel)
                else:
                    plan.active_vc_channel_id = None  # stale, recreate below
            if not plan.active_vc_channel_id:
                vc_channel = await self._spawn_event_vc(guild, plan, locked=False)
                if vc_channel:
                    plan.active_vc_channel_id = vc_channel.id
                    await plan.asave(update_fields=["active_vc_channel_id"])

        # 3. Start the Discord native scheduled event
        if plan.discord_scheduled_event_id:
            try:
                discord_event = guild.get_scheduled_event(plan.discord_scheduled_event_id)
                if not discord_event:
                    discord_event = await guild.fetch_scheduled_event(plan.discord_scheduled_event_id)
                if discord_event and discord_event.status == discord.EventStatus.scheduled:
                    await discord_event.start()
            except (discord.NotFound, discord.HTTPException):
                pass

        # 4. Start disfunction attendance tracking
        import uuid
        attendance_session_id = str(uuid.uuid4())
        # Resolve organizer Discord ID (created_by_id is a ULID OrgPlayer PK)
        organizer_discord_id = 0
        if plan.created_by_id:
            from asgiref.sync import sync_to_async
            from app.unifieduser.models import OrgPlayer
            try:
                creator = await OrgPlayer.objects.select_related("discorduser").aget(pk=plan.created_by_id)
                organizer_discord_id = getattr(getattr(creator, "discorduser", None), "discorduid", 0) or 0
            except Exception:
                pass
        await self.EventAttendance.objects.acreate(
            event_id=attendance_session_id,
            event_name=plan.title,
            event_type="SCHEDULED",
            organizer_id=organizer_discord_id,
            voice_channel_id=vc_channel.id if vc_channel else None,
            actual_start=timezone.now(),
        )

        # 5. Snapshot everyone already in the VC at start time
        #    Handles: pre-joined RSVP'd members, walk-ins, and MAYBE → GOING upgrades.
        #    on_voice_state_update handles joins that happen *after* this point.
        if vc_channel and vc_channel.members:
            attendance = await self.EventAttendance.objects.aget(event_id=attendance_session_id)
            now = timezone.now()
            late_threshold = await aget_global_preference(prefs.EventLateThresholdMinutes.import_path)
            late_cutoff = plan.planned_start_at + timedelta(minutes=int(late_threshold or 10))
            is_late = now > late_cutoff

            for member in vc_channel.members:
                if member.bot:
                    continue

                org_user = await self._get_org_user(member.id)
                if not org_user:
                    continue

                rsvp = await self.EventRSVP.objects.filter(
                    event=plan, user=org_user
                ).afirst()

                if rsvp:
                    if not rsvp.checked_in:
                        rsvp.checked_in = True
                        rsvp.check_in_time = now
                        rsvp.check_in_source = self.EventRSVP.CheckInSource.VC_JOIN
                        # Upgrade MAYBE → GOING since they showed up
                        if rsvp.status == self.EventRSVP.RSVPStatus.MAYBE:
                            rsvp.status = self.EventRSVP.RSVPStatus.GOING
                        if is_late and rsvp.attendance_outcome is None:
                            rsvp.attendance_outcome = self.EventRSVP.AttendanceOutcome.LATE
                        await rsvp.asave(update_fields=[
                            "checked_in", "check_in_time", "check_in_source",
                            "status", "attendance_outcome",
                        ])
                else:
                    # Walk-in: no RSVP but present in VC at start
                    await self.EventRSVP.objects.acreate(
                        event=plan,
                        user=org_user,
                        status=self.EventRSVP.RSVPStatus.GOING,
                        checked_in=True,
                        check_in_time=now,
                        check_in_source=self.EventRSVP.CheckInSource.VC_JOIN,
                        attendance_outcome=self.EventRSVP.AttendanceOutcome.LATE if is_late else None,
                    )

                await self.EventAttendanceRecord.objects.aget_or_create(
                    event=attendance,
                    user_id=member.id,
                    defaults={"status": "ACTIVE", "join_time": now},
                )

        # 6. Notify attendee thread (ping mentions_on_start roles)
        mention_content, allowed = _mentions_from_role_ids(plan.mentions_on_start)
        if plan.attendee_thread_id:
            thread = guild.get_thread(plan.attendee_thread_id)
            if not thread:
                try:
                    thread = await guild.fetch_channel(plan.attendee_thread_id)
                except Exception:
                    thread = None
            if thread:
                vc_mention = vc_channel.mention if vc_channel else "*(no VC)*"
                body = (
                    f"🟢 **{plan.title}** has started!\n"
                    f"Voice Channel: {vc_mention}\n"
                    f"Ends <t:{int(plan.planned_end_at.timestamp())}:R>."
                )
                await thread.send(
                    content=f"{mention_content}\n{body}" if mention_content else body,
                    allowed_mentions=allowed,
                )

        # 6b. Also ping mentions_on_start in the announcement channel (public nudge)
        if mention_content and plan.announcement_channel_id:
            ann_ch = guild.get_channel(plan.announcement_channel_id)
            if ann_ch:
                try:
                    await ann_ch.send(
                        content=(
                            f"{mention_content}\n"
                            f"🟢 **{plan.indicator.prefix} {plan.title}** is starting now — "
                            f"jump into {vc_channel.mention if vc_channel else 'the event'}!"
                        ),
                        allowed_mentions=allowed,
                    )
                except (discord.HTTPException, discord.Forbidden):
                    pass

        # 7. Update announcement embed to show ACTIVE status
        await self._refresh_announcement_embed(plan)

        self.logger.info("Event %s started (VC: %s)", plan.codename_id, vc_channel.id if vc_channel else None)

    async def _end_event(self, plan):
        """
        Transitions ACTIVE → COMPLETED.
        Closes attendance, deletes event VC, archives threads, triggers report.
        """
        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            return

        # 1. Close disfunction attendance session
        attendance_session_id = None
        if plan.active_vc_channel_id:
            attendance = await self.EventAttendance.objects.filter(
                voice_channel_id=plan.active_vc_channel_id,
                actual_end__isnull=True,
            ).afirst()
            if attendance:
                attendance.actual_end = timezone.now()
                await attendance.asave(update_fields=["actual_end"])
                attendance_session_id = attendance.event_id

        # 2. Delete event VC
        if plan.active_vc_channel_id:
            vc = guild.get_channel(plan.active_vc_channel_id)
            if vc:
                try:
                    await vc.delete(reason=f"Event {plan.codename_id} ended")
                except (discord.HTTPException, discord.Forbidden):
                    pass
            plan.active_vc_channel_id = None
            await plan.asave(update_fields=["active_vc_channel_id"])

        # 3. End the Discord native scheduled event
        if plan.discord_scheduled_event_id:
            try:
                discord_event = guild.get_scheduled_event(plan.discord_scheduled_event_id)
                if not discord_event:
                    discord_event = await guild.fetch_scheduled_event(plan.discord_scheduled_event_id)
                if discord_event and discord_event.status == discord.EventStatus.active:
                    await discord_event.end()
            except (discord.NotFound, discord.HTTPException):
                pass

        # 5. Notify, lock, and archive attendee thread
        if plan.attendee_thread_id:
            thread = guild.get_thread(plan.attendee_thread_id)
            if thread:
                try:
                    await thread.send(f"🏁 **{plan.title}** has ended. Thanks for joining!")
                    await thread.edit(locked=True, archived=True)
                except Exception:
                    pass

        # 6. Update announcement embed
        await self._refresh_announcement_embed(plan)

        # 7. Generate report via Celery
        from app.schedevents.tasks import generate_event_report
        try:
            await sync_to_async(generate_event_report.delay)(
                plan.codename_id, attendance_session_id
            )
            self.logger.info("Event %s: report task enqueued (session=%s)", plan.codename_id, attendance_session_id)
        except Exception:
            self.logger.exception("Event %s: failed to enqueue generate_event_report", plan.codename_id)

        self.logger.info("Event %s ended", plan.codename_id)

    async def _on_cancelled(self, plan):
        """Cancel the Discord native event and DM GOING RSVPs."""
        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            return

        # 1. Cancel Discord native event
        if plan.discord_scheduled_event_id:
            try:
                discord_event = guild.get_scheduled_event(plan.discord_scheduled_event_id)
                if not discord_event:
                    discord_event = await guild.fetch_scheduled_event(plan.discord_scheduled_event_id)
                if discord_event and discord_event.status not in (
                    discord.EventStatus.cancelled,
                    discord.EventStatus.ended,
                ):
                    await discord_event.cancel()
            except (discord.NotFound, discord.HTTPException):
                pass

        # 2. Delete locked pre-created VC (if it was never opened)
        if plan.active_vc_channel_id:
            vc = guild.get_channel(plan.active_vc_channel_id)
            if vc:
                try:
                    await vc.delete(reason=f"Event {plan.codename_id} cancelled")
                except (discord.HTTPException, discord.Forbidden):
                    pass
            plan.active_vc_channel_id = None
            await plan.asave(update_fields=["active_vc_channel_id"])

        # 3. Update announcement embed
        await self._refresh_announcement_embed(plan)

        # 4. Lock and archive attendee thread
        if plan.attendee_thread_id:
            thread = guild.get_thread(plan.attendee_thread_id)
            if thread:
                try:
                    ts_str = f"<t:{int(plan.planned_start_at.timestamp())}:F>"
                    await thread.send(f"❌ **{plan.title}** ({ts_str}) has been **cancelled**.")
                    await thread.edit(locked=True, archived=True)
                except (discord.HTTPException, discord.Forbidden):
                    pass

        # 5. DM GOING/MAYBE RSVPs
        ts = int(plan.planned_start_at.timestamp())
        msg = (
            f"📢 **{plan.title}** (scheduled for <t:{ts}:F>) has been **cancelled**.\n"
            f"Event ID: `{plan.codename_id}`"
        )
        async for rsvp in self.EventRSVP.objects.filter(
            event=plan,
            status__in=[self.EventRSVP.RSVPStatus.GOING, self.EventRSVP.RSVPStatus.MAYBE],
        ).select_related("user__discorduser"):
            discord_id = getattr(getattr(rsvp.user, "discorduser", None), "discorduid", None)
            member = guild.get_member(discord_id) if discord_id else None
            if member:
                try:
                    await member.send(msg)
                except discord.Forbidden:
                    pass

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _pre_create_event_vc(self, plan, locked: bool = True):
        """
        Create the event VC and save its ID.
        locked=True  → called ~1h before start (VC locked until _unlock_event_vc).
        locked=False → called in the 30-min window when the event was published late.
        """
        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            return

        vc_channel = await self._spawn_event_vc(guild, plan, locked=locked)
        if not vc_channel:
            return

        plan.active_vc_channel_id = vc_channel.id
        await plan.asave(update_fields=["active_vc_channel_id"])

        # Re-point the Discord scheduled event to the new VC
        if plan.discord_scheduled_event_id:
            try:
                discord_event = await guild.fetch_scheduled_event(plan.discord_scheduled_event_id)
                await discord_event.edit(
                    channel=vc_channel,
                    entity_type=discord.EntityType.voice,
                    reason="Event VC created 1h before start",
                )
            except (discord.HTTPException, discord.Forbidden):
                self.logger.warning("Could not re-point Discord event to VC for %s", plan.codename_id)

        # Notify the attendee thread (ping mentions_on_vc_unlock only when actually unlocking)
        if plan.attendee_thread_id:
            thread = guild.get_thread(plan.attendee_thread_id)
            if thread:
                ts = int(plan.planned_start_at.timestamp())
                try:
                    if locked:
                        await thread.send(
                            f"🔒 **Voice channel created:** {vc_channel.mention}\n"
                            f"It's locked and will open <t:{ts - 1800}:R> (30 min before start)."
                        )
                    else:
                        mention_content, allowed = _mentions_from_role_ids(plan.mentions_on_vc_unlock)
                        body = f"🔊 **Voice channel is open:** {vc_channel.mention}"
                        await thread.send(
                            content=f"{mention_content}\n{body}" if mention_content else body,
                            allowed_mentions=allowed,
                        )
                except (discord.HTTPException, discord.Forbidden):
                    pass

        self.logger.info(
            "Pre-created %s event VC %s for plan %s",
            "locked" if locked else "unlocked", vc_channel.id, plan.codename_id,
        )

    async def _spawn_event_vc(self, guild: discord.Guild, plan, locked: bool = False) -> discord.VoiceChannel | None:
        """Creates the event voice channel under the configured parent.
        If locked=True, @everyone cannot connect until _unlock_event_vc is called.
        """
        from app.disfunction.models import VoiceChannelProfile, VCRolePermission
        from asgiref.sync import sync_to_async

        # Resolve profile
        profile = plan.vc_profile
        if not profile:
            default_slug = await aget_global_preference(prefs.EventDefaultVCProfileSlug.import_path)
            if default_slug:
                profile = await VoiceChannelProfile.objects.filter(
                    slug=default_slug, is_active=True
                ).afirst()
            if not profile:
                profile = await VoiceChannelProfile.objects.filter(is_active=True).order_by("sort_order").afirst()

        # Resolve parent category
        category = None
        cat_id = await aget_global_preference(prefs.EventVCParentCategoryID.import_path)
        if cat_id:
            category = guild.get_channel(int(cat_id))

        # Build permission overwrites from VCRolePermission rows
        overwrites: dict | None = None
        if profile:
            rows = await sync_to_async(list)(
                VCRolePermission.objects.filter(profile=profile)
            )
            if rows:
                overwrites = {}
                for row in rows:
                    if row.discord_role_id == 0:
                        target = guild.default_role
                    else:
                        target = guild.get_role(row.discord_role_id)
                    if target is None:
                        continue
                    overwrites[target] = discord.PermissionOverwrite(**row.to_discord_overwrite(locked=locked))

        # Ensure @everyone is always in the overwrites with connect denied
        if overwrites is None:
            overwrites = {}
        if guild.default_role not in overwrites:
            overwrites[guild.default_role] = discord.PermissionOverwrite(connect=False)

        prefix = "🔒" if locked else "🎯"
        channel_name = f"{prefix} {plan.indicator.prefix} {plan.title}"[:100]

        try:
            vc = await guild.create_voice_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Event VC for {plan.codename_id}",
            )
            return vc
        except (discord.HTTPException, discord.Forbidden):
            self.logger.exception("Failed to create event VC for %s", plan.codename_id)
            return None

    async def _unlock_event_vc(self, plan, vc_channel: discord.VoiceChannel | None = None):
        """Unlocks the event VC 30 min before start.

        Re-applies VCRolePermission rows with locked=False so each role gets
        its configured unlocked permissions. @everyone role_id=0 row controls
        whether @everyone can connect; if no such row exists, connect stays denied.
        """
        from app.disfunction.models import VCRolePermission
        from asgiref.sync import sync_to_async

        if not vc_channel:
            guild = self.client.get_guild(plan.guild_id)
            if not guild or not plan.active_vc_channel_id:
                return
            vc_channel = guild.get_channel(plan.active_vc_channel_id)
        if not vc_channel:
            return

        guild = vc_channel.guild

        # Already unlocked — skip
        if vc_channel.overwrites_for(guild.default_role).connect is not False:
            return

        profile = plan.vc_profile
        rows = []
        if profile:
            rows = await sync_to_async(list)(
                VCRolePermission.objects.filter(profile=profile)
            )

        try:
            if rows:
                everyone_handled = False
                for row in rows:
                    if row.discord_role_id == 0:
                        target = guild.default_role
                        everyone_handled = True
                    else:
                        target = guild.get_role(row.discord_role_id)
                    if target is None:
                        continue
                    await vc_channel.set_permissions(
                        target,
                        reason="Event VC unlock",
                        **row.to_discord_overwrite(locked=False),
                    )
                # If no row for @everyone, keep connect denied
                if not everyone_handled:
                    await vc_channel.set_permissions(guild.default_role, connect=False, reason="Event VC unlock")
            else:
                # No VCRolePermission rows — fall back: grant connect to all existing overwrites except @everyone
                for target, overwrite in list(vc_channel.overwrites.items()):
                    if target == guild.default_role:
                        continue
                    await vc_channel.set_permissions(target, connect=True, reason="Event VC unlock")
                await vc_channel.set_permissions(guild.default_role, connect=False, reason="Event VC unlock")

            # Rename 🔒 → 🎯
            new_name = vc_channel.name.replace("🔒", "🎯", 1)
            if new_name != vc_channel.name:
                await vc_channel.edit(name=new_name, reason="Event VC unlock")

            # Ping mentions_on_vc_unlock in the attendee thread
            mention_content, allowed = _mentions_from_role_ids(plan.mentions_on_vc_unlock)
            if mention_content and plan.attendee_thread_id:
                thread = guild.get_thread(plan.attendee_thread_id)
                if not thread:
                    try:
                        thread = await guild.fetch_channel(plan.attendee_thread_id)
                    except Exception:
                        thread = None
                if thread:
                    try:
                        ts = int(plan.planned_start_at.timestamp())
                        await thread.send(
                            content=(
                                f"{mention_content}\n"
                                f"🔓 **{vc_channel.mention} is now open** — event starts <t:{ts}:R>."
                            ),
                            allowed_mentions=allowed,
                        )
                    except (discord.HTTPException, discord.Forbidden):
                        pass

            self.logger.info("Unlocked event VC %s for plan %s", vc_channel.id, plan.codename_id)
        except (discord.HTTPException, discord.Forbidden):
            self.logger.exception("Failed to unlock event VC for %s", plan.codename_id)

    @staticmethod
    def _build_discord_description(plan) -> str:
        from django.conf import settings
        site_url = getattr(settings, "SITE_URL", "https://blightveil.org/").rstrip("/")
        event_url = f"{site_url}/events/{plan.codename_id}/"
        prefix = f"Event Details and Sign Up Link: {event_url}"
        body = (plan.description or "").strip()
        full = f"{prefix}\n\n{body}" if body else prefix
        return full[:1000]  # Discord scheduled event description cap

    async def _create_discord_scheduled_event(
        self, guild: discord.Guild, plan, vc_channel: discord.VoiceChannel | None = None
    ) -> discord.ScheduledEvent | None:
        """Creates a GuildScheduledEvent for the EventPlan.

        If vc_channel is provided the scheduled event points directly at it.
        Otherwise falls back to EXTERNAL entity type.
        """
        cover_bytes = await self._fetch_cover_bytes(plan.cover_image_url) if plan.cover_image_url else None

        try:
            if vc_channel:
                kwargs = dict(
                    name=f"{plan.indicator.prefix} {plan.title}",
                    description=self._build_discord_description(plan),
                    start_time=plan.planned_start_at,
                    end_time=plan.planned_end_at,
                    entity_type=discord.EntityType.voice,
                    channel=vc_channel,
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
                if cover_bytes:
                    kwargs["image"] = cover_bytes
                return await guild.create_scheduled_event(**kwargs)
            else:
                kwargs = dict(
                    name=f"{plan.indicator.prefix} {plan.title}",
                    description=self._build_discord_description(plan),
                    start_time=plan.planned_start_at,
                    end_time=plan.planned_end_at,
                    entity_type=discord.EntityType.external,
                    location=plan.location or "TBD",
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
                if cover_bytes:
                    kwargs["image"] = cover_bytes
                return await guild.create_scheduled_event(**kwargs)
        except (discord.HTTPException, discord.Forbidden):
            self.logger.exception("Failed to create Discord scheduled event for %s", plan.codename_id)
            return None

    async def _refresh_announcement_embed(self, plan):
        """Updates the announcement embed in-place with current RSVP counts."""
        if not plan.announcement_channel_id or not plan.announcement_message_id:
            return
        guild = self.client.get_guild(plan.guild_id)
        if not guild:
            return
        ch = guild.get_channel(plan.announcement_channel_id)
        if not ch:
            return
        try:
            msg = await ch.fetch_message(plan.announcement_message_id)
            embed = await _build_event_embed(plan)
            is_closed = plan.status in (
                self.EventPlan.Status.COMPLETED,
                self.EventPlan.Status.CANCELLED,
            )
            view = await get_rsvp_view(plan, is_closed=is_closed)
            await msg.edit(embed=embed, view=view)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _sync_thread_member(self, guild_id, thread_id, discord_uid, status):
        """Add or remove a member from the attendee thread based on their RSVP status."""
        if not thread_id or not discord_uid or not guild_id:
            return

        guild = self.client.get_guild(guild_id)
        if not guild:
            return

        member = guild.get_member(discord_uid)
        if not member:
            return

        thread = guild.get_thread(thread_id)
        if not thread:
            try:
                thread = await guild.fetch_channel(thread_id)
            except Exception:
                return

        # Locked/archived threads can't have members added
        if getattr(thread, "archived", False) or getattr(thread, "locked", False):
            return

        going_statuses = ("GOING", "MAYBE", "WAITLISTED")
        try:
            if status in going_statuses:
                await thread.add_user(member)
            else:
                await thread.remove_user(member)
        except (discord.HTTPException, discord.Forbidden):
            pass

    async def _upsert_rsvp(self, plan, org_user, status, source=None):
        from app.schedevents.models import EventRSVP
        rsvp, created = await self.EventRSVP.objects.aupdate_or_create(
            event=plan,
            user=org_user,
            defaults={"status": status},
        )
        return rsvp

    async def _plan_for_discord_event(self, discord_event_id: int):
        return await self.EventPlan.objects.select_related("indicator", "vc_profile").filter(
            discord_scheduled_event_id=discord_event_id
        ).afirst()

    async def _get_org_user(self, discord_user_id: int):
        from app.unifieduser.models import OrgPlayer
        try:
            return await OrgPlayer.objects.aget(discorduser__discorduid=discord_user_id)
        except Exception:
            return None

    async def _fetch_cover_bytes(self, url: str) -> bytes | None:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        return await resp.read()
        except Exception:
            pass
        return None


async def setup(client):
    await client.add_cog(EventLifecycleCog(client))
