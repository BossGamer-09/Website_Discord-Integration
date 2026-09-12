"""
app/schedevents/cogs/event_manager.py

Slash command group /event — CRUD interface for scheduled events.

Commands:
  /event create              — multi-step modal → draft EventPlan
  /event publish <codename>  — publish a draft (creates Discord native event)
  /event edit <codename>     — edit title/description/timing (modal)
  /event cancel <codename>   — cancel with optional reason
  /event list                — paginated upcoming events
  /event info <codename>     — detailed embed with live RSVP buttons
  /event roster <codename>   — who's going / maybe / waitlisted

RSVP buttons live in event_rsvp.py; this cog only handles the command surface.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands
from django.apps import apps
from django.utils import timezone

from app.main.util.discord_command_checks import requires_django_perm
from app.preferences.utils import aget_global_preference
from app.schedevents import preferences as prefs

log = logging.getLogger(__name__)

# How many events to show per /event list page
LIST_PAGE_SIZE = 5


# ---------------------------------------------------------------------------
# Shared embed builder
# ---------------------------------------------------------------------------

def build_event_embed(plan, going_users=None, maybe_users=None) -> discord.Embed:
    """
    Builds the standard Sesh-style announcement embed for an EventPlan.
    going_users / maybe_users: optional lists of display strings (non-anonymous).
    """
    from app.schedevents.models import EventRSVP

    color = plan.indicator.discord_color

    embed = discord.Embed(
        title=f"{plan.indicator.prefix} {plan.title}",
        description=plan.description or "",
        color=color,
    )

    # Timing
    ts = int(plan.planned_start_at.timestamp())
    end_ts = int(plan.planned_end_at.timestamp())
    embed.add_field(
        name="📅 Date & Time",
        value=f"<t:{ts}:F>\n<t:{ts}:R>",
        inline=True,
    )
    embed.add_field(
        name="⏱️ Duration",
        value=_format_duration(plan.planned_duration),
        inline=True,
    )
    if plan.timezone_name:
        embed.add_field(name="🌐 Timezone", value=plan.timezone_name, inline=True)

    if plan.location:
        embed.add_field(name="📍 Location", value=plan.location, inline=False)

    # Capacity
    going = plan.going_count
    cap_str = f"{going} / {plan.capacity}" if plan.capacity else str(going)
    waitlist = plan.waitlist_count
    cap_line = f"**{cap_str}** going"
    if plan.capacity and waitlist > 0:
        cap_line += f"  •  **{waitlist}** waitlisted"
    elif plan.capacity:
        remaining = plan.capacity - going
        if remaining > 0:
            cap_line += f"  •  {remaining} spots left"
        else:
            cap_line += "  •  **FULL**" + (" (waitlist open)" if plan.waitlist_enabled else "")
    embed.add_field(name="👥 Attendance", value=cap_line, inline=False)

    def _names(qs, limit=10):
        names = []
        for r in qs[:limit]:
            du = getattr(r.user, "discorduser", None)
            names.append(f"<@{du.discorduid}>" if du else r.user.display_name)
        total = qs.count()
        if total > limit:
            names.append(f"*+{total - limit} more*")
        return ", ".join(names) if names else "*None yet*"

    # RSVP lists — per option when configured, else standard Going/Maybe
    from app.schedevents.models import RSVPOption
    from django.db.models import Q

    all_options = list(RSVPOption.objects.filter(
        Q(event=plan) | Q(event=None, guild_id=plan.guild_id),
        is_active=True,
    ).order_by("sort_order", "id"))
    event_options = [o for o in all_options if o.event_id is not None]
    options = event_options or [o for o in all_options if o.event_id is None]

    if options:
        for opt in options:
            opt_rsvps = plan.rsvps.filter(
                rsvp_option=opt, is_anonymous=False,
            ).select_related("user__discorduser")
            count = plan.rsvps.filter(rsvp_option=opt).count()
            embed.add_field(
                name=f"{opt.emoji} {opt.label} ({count})",
                value=_names(opt_rsvps),
                inline=False,
            )
    else:
        going_rsvps = plan.rsvps.filter(
            status=EventRSVP.RSVPStatus.GOING, is_anonymous=False,
        ).select_related("user__discorduser")
        maybe_rsvps = plan.rsvps.filter(
            status=EventRSVP.RSVPStatus.MAYBE, is_anonymous=False,
        ).select_related("user__discorduser")
        embed.add_field(name=f"✅ Going ({going})", value=_names(going_rsvps), inline=False)
        maybe_count = plan.maybe_count
        embed.add_field(name=f"❓ Maybe ({maybe_count})", value=_names(maybe_rsvps), inline=False)

    embed.set_footer(text=f"Event ID: {plan.codename_id}  •  Status: {plan.get_status_display()}")

    if plan.cover_image_url:
        embed.set_image(url=plan.cover_image_url)

    return embed


def _format_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    h, m = divmod(total // 60, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _parse_datetime(date_str: str, time_str: str, tz_name: str) -> datetime | None:
    """Parse 'YYYY-MM-DD' + 'HH:MM' in the given IANA timezone → UTC-aware datetime."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        tz = dt_timezone.utc
    try:
        local_dt = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%Y-%m-%d %H:%M")
        return local_dt.replace(tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return None


def _parse_duration(value: str) -> timedelta | None:
    """Parse '2h', '90m', '1h30m', '120' (minutes) → timedelta."""
    value = value.strip().lower()
    try:
        if "h" in value and "m" in value:
            h, m = value.split("h")
            return timedelta(hours=int(h), minutes=int(m.replace("m", "")))
        if "h" in value:
            return timedelta(hours=int(value.replace("h", "")))
        if "m" in value:
            return timedelta(minutes=int(value.replace("m", "")))
        return timedelta(minutes=int(value))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class EventCreateModal(discord.ui.Modal, title="Create New Event"):
    event_title = discord.ui.TextInput(
        label="Title", max_length=200, required=True,
        placeholder="e.g. Friday Night Op"
    )
    description = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph,
        max_length=2000, required=False,
        placeholder="What's this event about?"
    )
    date = discord.ui.TextInput(
        label="Date (YYYY-MM-DD)", max_length=10, required=True,
        placeholder="2025-01-25"
    )
    time = discord.ui.TextInput(
        label="Time (HH:MM, 24h)", max_length=5, required=True,
        placeholder="20:00"
    )
    duration = discord.ui.TextInput(
        label="Duration (e.g. 2h, 90m, 1h30m)", max_length=10,
        required=False, placeholder="2h"
    )

    def __init__(self, cog, indicator_id: str, tz_name: str, capacity_str: str):
        super().__init__()
        self.cog = cog
        self.indicator_id = indicator_id
        self.tz_name = tz_name or "UTC"
        self.capacity_str = capacity_str

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.cog.finalize_create(interaction, self)


class EventEditModal(discord.ui.Modal, title="Edit Event"):
    new_title = discord.ui.TextInput(
        label="Title", max_length=200, required=True
    )
    new_description = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph,
        max_length=2000, required=False
    )
    new_date = discord.ui.TextInput(
        label="Date (YYYY-MM-DD)", max_length=10, required=True
    )
    new_time = discord.ui.TextInput(
        label="Time (HH:MM, 24h)", max_length=5, required=True
    )
    new_duration = discord.ui.TextInput(
        label="Duration (e.g. 2h, 90m)", max_length=10, required=False
    )

    def __init__(self, cog, codename_id: str):
        super().__init__()
        self.cog = cog
        self.codename_id = codename_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.cog.finalize_edit(interaction, self)


# ---------------------------------------------------------------------------
# Creation setup view — indicator picker + extras before the modal
# ---------------------------------------------------------------------------

class EventCreateSetupView(discord.ui.View):
    def __init__(self, cog, indicators):
        super().__init__(timeout=120)
        self.cog = cog
        self.indicator_id = indicators[0].id if indicators else "PUBLIC"
        self.tz_name = "UTC"
        self.capacity_str = ""

        # Indicator select
        options = [
            discord.SelectOption(
                label=f"{ind.prefix} {ind.name}",
                value=ind.id,
                description=ind.description[:50] if ind.description else None,
            )
            for ind in indicators[:25]
        ]
        ind_select = discord.ui.Select(
            placeholder="Select event indicator/classification…",
            options=options,
            custom_id="sched_setup_indicator",
        )
        ind_select.callback = self._on_indicator
        self.add_item(ind_select)

        tz_input = discord.ui.Button(
            label=f"Timezone: {self.tz_name}",
            style=discord.ButtonStyle.secondary,
            custom_id="sched_setup_tz",
        )
        tz_input.callback = self._on_tz
        self.add_item(tz_input)

        cap_input = discord.ui.Button(
            label="Capacity: Unlimited",
            style=discord.ButtonStyle.secondary,
            custom_id="sched_setup_cap",
        )
        cap_input.callback = self._on_cap
        self.add_item(cap_input)

        next_btn = discord.ui.Button(
            label="Next →",
            style=discord.ButtonStyle.primary,
            custom_id="sched_setup_next",
        )
        next_btn.callback = self._on_next
        self.add_item(next_btn)

    async def _on_indicator(self, interaction: discord.Interaction):
        self.indicator_id = interaction.data["values"][0]
        await interaction.response.defer()

    async def _on_tz(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            _TZModal(self)
        )

    async def _on_cap(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            _CapModal(self)
        )

    async def _on_next(self, interaction: discord.Interaction):
        modal = EventCreateModal(
            self.cog,
            indicator_id=self.indicator_id,
            tz_name=self.tz_name,
            capacity_str=self.capacity_str,
        )
        await interaction.response.send_modal(modal)


class _TZModal(discord.ui.Modal, title="Set Timezone"):
    tz = discord.ui.TextInput(
        label="IANA Timezone (e.g. America/New_York)",
        placeholder="UTC",
        max_length=64,
        required=True,
    )

    def __init__(self, setup_view: EventCreateSetupView):
        super().__init__()
        self.setup_view = setup_view

    async def on_submit(self, interaction: discord.Interaction):
        tz_name = self.tz.value.strip()
        try:
            ZoneInfo(tz_name)
            self.setup_view.tz_name = tz_name
            await interaction.response.send_message(f"Timezone set to **{tz_name}**.", ephemeral=True)
        except Exception:
            await interaction.response.send_message(
                f"❌ Unknown timezone `{tz_name}`. Use an IANA name like `America/New_York`.",
                ephemeral=True,
            )


class _CapModal(discord.ui.Modal, title="Set Capacity"):
    cap = discord.ui.TextInput(
        label="Max attendees (blank = unlimited)",
        placeholder="20",
        max_length=6,
        required=False,
    )

    def __init__(self, setup_view: EventCreateSetupView):
        super().__init__()
        self.setup_view = setup_view

    async def on_submit(self, interaction: discord.Interaction):
        self.setup_view.capacity_str = self.cap.value.strip()
        label = f"Capacity: {self.cap.value.strip()}" if self.cap.value.strip() else "Capacity: Unlimited"
        await interaction.response.send_message(f"Capacity set to **{label}**.", ephemeral=True)


# ---------------------------------------------------------------------------
# List pagination view
# ---------------------------------------------------------------------------

class EventListView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], author_id: int):
        super().__init__(timeout=120)
        self.pages = pages
        self.current = 0
        self.author_id = author_id
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)


# ---------------------------------------------------------------------------
# EventManagerCog
# ---------------------------------------------------------------------------

class EventManagerCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.EventPlan = apps.get_model("schedevents", "EventPlan")
        self.EventRSVP = apps.get_model("schedevents", "EventRSVP")
        self.EventIndicator = apps.get_model("schedevents", "EventIndicator")
        self.EventReminder = apps.get_model("schedevents", "EventReminder")

    event_group = app_commands.Group(
        name="event",
        description="Manage and view scheduled org events",
    )

    # ------------------------------------------------------------------ #
    # /event create
    # ------------------------------------------------------------------ #

    @event_group.command(name="create", description="Create a new scheduled event")
    @requires_django_perm("schedevents.add_eventplan")
    async def cmd_create(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        indicators = [i async for i in self.EventIndicator.objects.all().order_by("id")]
        if not indicators:
            await interaction.followup.send(
                "❌ No event indicators configured. Add some in the Django admin first.",
                ephemeral=True,
            )
            return

        view = EventCreateSetupView(self, indicators)
        await interaction.followup.send(
            "**Step 1 of 2** — Choose event classification, timezone, and capacity, then click **Next →** to fill in the details.",
            view=view,
            ephemeral=True,
        )

    async def finalize_create(self, interaction: discord.Interaction, modal: EventCreateModal):
        """Called from EventCreateModal.on_submit."""
        from asgiref.sync import sync_to_async

        # Parse timing
        start_dt = _parse_datetime(modal.date.value, modal.time.value, modal.tz_name)
        if not start_dt:
            await interaction.followup.send(
                "❌ Invalid date/time. Use `YYYY-MM-DD` for date and `HH:MM` (24h) for time.",
                ephemeral=True,
            )
            return

        if start_dt < timezone.now():
            await interaction.followup.send("❌ Event start time is in the past.", ephemeral=True)
            return

        duration = _parse_duration(modal.duration.value) if modal.duration.value else timedelta(hours=2)
        if not duration or duration.total_seconds() < 60:
            await interaction.followup.send(
                "❌ Invalid duration. Use formats like `2h`, `90m`, or `1h30m`.",
                ephemeral=True,
            )
            return

        capacity = None
        if modal.capacity_str:
            try:
                capacity = int(modal.capacity_str)
            except ValueError:
                await interaction.followup.send("❌ Capacity must be a number.", ephemeral=True)
                return

        # Resolve indicator
        try:
            indicator = await self.EventIndicator.objects.aget(id=modal.indicator_id)
        except self.EventIndicator.DoesNotExist:
            await interaction.followup.send("❌ Selected indicator no longer exists.", ephemeral=True)
            return

        # Resolve OrgPlayer for created_by
        from app.unifieduser.models import OrgPlayer
        try:
            org_user = await OrgPlayer.objects.aget(discorduser__discorduid=interaction.user.id)
        except OrgPlayer.DoesNotExist:
            org_user = None

        # Create draft EventPlan
        plan = await self.EventPlan.objects.acreate(
            title=modal.event_title.value.strip(),
            description=modal.description.value.strip() if modal.description.value else "",
            indicator=indicator,
            created_by=org_user,
            planned_start_at=start_dt,
            planned_duration=duration,
            timezone_name=modal.tz_name,
            guild_id=interaction.guild.id,
            capacity=capacity,
            waitlist_enabled=True,
        )

        # Add default reminder if configured
        default_mins = await aget_global_preference(prefs.EventDefaultReminderMinutes.import_path)
        if default_mins:
            await self.EventReminder.objects.acreate(
                event=plan,
                minutes_before=int(default_mins),
                target="DM",
            )

        embed = await sync_to_async(build_event_embed)(plan)
        embed.set_footer(text=f"Event ID: {plan.codename_id}  •  Status: DRAFT — use /event publish to go live")

        await interaction.followup.send(
            f"✅ Draft event **{plan.codename_id}** created!\n"
            f"Use `/event publish {plan.codename_id}` to announce it, or edit it in the admin panel.",
            embed=embed,
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # /event publish
    # ------------------------------------------------------------------ #

    @event_group.command(name="publish", description="Publish a draft event (announces it + creates Discord event)")
    @app_commands.describe(codename="The event codename ID (e.g. BraveRedStar)")
    @requires_django_perm("schedevents.can_publish_events")
    async def cmd_publish(self, interaction: discord.Interaction, codename: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        plan = await self._get_plan_or_error(interaction, codename)
        if not plan:
            return

        if plan.status != self.EventPlan.Status.DRAFT:
            await interaction.followup.send(
                f"❌ Event `{codename}` is not a draft (current status: {plan.get_status_display()}).",
                ephemeral=True,
            )
            return

        plan.status = self.EventPlan.Status.PUBLISHED
        await plan.asave(update_fields=["status"])
        # Signal → EventLifecycleCog creates Discord scheduled event + posts announcement embed

        await interaction.followup.send(
            f"✅ Event **{plan.codename_id}** published! The bot will post the announcement shortly.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # /event edit
    # ------------------------------------------------------------------ #

    @event_group.command(name="edit", description="Edit an event's title, description, or timing")
    @app_commands.describe(codename="The event codename ID")
    async def cmd_edit(self, interaction: discord.Interaction, codename: str):
        plan = await self._get_plan_or_error(interaction, codename)
        if not plan:
            return

        if not await self._check_edit_perm(interaction, plan):
            return

        if plan.status in (self.EventPlan.Status.COMPLETED, self.EventPlan.Status.CANCELLED):
            await interaction.response.send_message(
                f"❌ Cannot edit a {plan.get_status_display()} event.", ephemeral=True
            )
            return

        modal = EventEditModal(self, codename)
        modal.new_title.default = plan.title
        modal.new_description.default = plan.description
        modal.new_date.default = plan.planned_start_at.strftime("%Y-%m-%d")
        modal.new_time.default = plan.planned_start_at.strftime("%H:%M")
        modal.new_duration.default = _format_duration(plan.planned_duration)
        await interaction.response.send_modal(modal)

    async def finalize_edit(self, interaction: discord.Interaction, modal: EventEditModal):
        plan = await self._get_plan_or_error(interaction, modal.codename_id)
        if not plan:
            return

        start_dt = _parse_datetime(modal.new_date.value, modal.new_time.value, plan.timezone_name)
        if not start_dt:
            await interaction.followup.send("❌ Invalid date/time format.", ephemeral=True)
            return

        duration = _parse_duration(modal.new_duration.value) if modal.new_duration.value else plan.planned_duration
        if not duration:
            await interaction.followup.send("❌ Invalid duration format.", ephemeral=True)
            return

        plan.title = modal.new_title.value.strip()
        plan.description = modal.new_description.value.strip() if modal.new_description.value else ""
        plan.planned_start_at = start_dt
        plan.planned_duration = duration
        await plan.asave(update_fields=["title", "description", "planned_start_at", "planned_duration"])
        # Signal → EventLifecycleCog updates Discord event + refreshes announcement embed

        await interaction.followup.send(
            f"✅ Event **{plan.codename_id}** updated.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /event cancel
    # ------------------------------------------------------------------ #

    @event_group.command(name="cancel", description="Cancel a scheduled event")
    @app_commands.describe(
        codename="The event codename ID",
        reason="Optional cancellation reason (sent to GOING RSVPs)",
    )
    async def cmd_cancel(
        self, interaction: discord.Interaction, codename: str, reason: str = ""
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        plan = await self._get_plan_or_error(interaction, codename)
        if not plan:
            return

        if not await self._check_edit_perm(interaction, plan):
            return

        if plan.status in (self.EventPlan.Status.COMPLETED, self.EventPlan.Status.CANCELLED):
            await interaction.followup.send(
                f"❌ Event is already {plan.get_status_display()}.", ephemeral=True
            )
            return

        plan.status = self.EventPlan.Status.CANCELLED
        await plan.asave(update_fields=["status"])
        # Signal → lifecycle cog cancels Discord event + DMs GOING RSVPs

        await interaction.followup.send(
            f"✅ Event **{plan.codename_id}** cancelled." + (f"\nReason: {reason}" if reason else ""),
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # /event list
    # ------------------------------------------------------------------ #

    @event_group.command(name="list", description="Browse upcoming published events")
    async def cmd_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False, thinking=True)

        now = timezone.now()
        events = [
            p async for p in self.EventPlan.objects.filter(
                status__in=[self.EventPlan.Status.PUBLISHED, self.EventPlan.Status.ACTIVE],
                planned_start_at__gte=now,
                guild_id=interaction.guild.id,
            ).order_by("planned_start_at").select_related("indicator")[:50]
        ]

        if not events:
            await interaction.followup.send("No upcoming events scheduled.", ephemeral=False)
            return

        # Split into pages of LIST_PAGE_SIZE
        pages = []
        for i in range(0, len(events), LIST_PAGE_SIZE):
            chunk = events[i : i + LIST_PAGE_SIZE]
            embed = discord.Embed(
                title="📅 Upcoming Events",
                color=discord.Color.blurple(),
                description=f"Page {i // LIST_PAGE_SIZE + 1} of {-(-len(events) // LIST_PAGE_SIZE)}",
            )
            for plan in chunk:
                ts = int(plan.planned_start_at.timestamp())
                going = await plan.rsvps.filter(status=self.EventRSVP.RSVPStatus.GOING).acount()
                cap_str = f"{going}/{plan.capacity}" if plan.capacity else str(going)
                status_icon = "🟢" if plan.status == self.EventPlan.Status.ACTIVE else "🔵"
                embed.add_field(
                    name=f"{status_icon} {plan.indicator.prefix} {plan.title}",
                    value=f"<t:{ts}:F> (<t:{ts}:R>)\n👥 {cap_str} going  •  `{plan.codename_id}`",
                    inline=False,
                )
            pages.append(embed)

        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0])
        else:
            view = EventListView(pages, interaction.user.id)
            await interaction.followup.send(embed=pages[0], view=view)

    # ------------------------------------------------------------------ #
    # /event info
    # ------------------------------------------------------------------ #

    @event_group.command(name="info", description="View event details and RSVP")
    @app_commands.describe(codename="The event codename ID")
    async def cmd_info(self, interaction: discord.Interaction, codename: str):
        await interaction.response.defer(ephemeral=False, thinking=True)

        plan = await self._get_plan_or_error(interaction, codename, ephemeral=False)
        if not plan:
            return

        embed = await sync_to_async(build_event_embed)(plan)

        from app.schedevents.cogs.event_rsvp import get_rsvp_view
        is_closed = plan.status in (
            self.EventPlan.Status.COMPLETED, self.EventPlan.Status.CANCELLED
        )
        view = await get_rsvp_view(plan, is_closed=is_closed)

        await interaction.followup.send(embed=embed, view=view)

    # ------------------------------------------------------------------ #
    # /event roster
    # ------------------------------------------------------------------ #

    @event_group.command(name="roster", description="View the RSVP roster for an event")
    @app_commands.describe(codename="The event codename ID")
    async def cmd_roster(self, interaction: discord.Interaction, codename: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        plan = await self._get_plan_or_error(interaction, codename)
        if not plan:
            return

        rsvps = [r async for r in self.EventRSVP.objects.filter(event=plan).select_related("user").order_by("rsvp_time")]

        buckets = {
            "GOING": [], "MAYBE": [], "NOT_GOING": [],
            "WAITLISTED": [], "PENDING": [],
        }
        for r in rsvps:
            name = "*anonymous*" if r.is_anonymous else f"<@{r.user_id}>"
            ci = " ✅" if r.checked_in else ""
            buckets.get(r.status, []).append(f"{name}{ci}")

        embed = discord.Embed(
            title=f"Roster — {plan.indicator.prefix} {plan.title}",
            color=plan.indicator.discord_color,
        )
        embed.set_footer(text=f"Event ID: {plan.codename_id}")

        labels = {
            "GOING": "✅ Going",
            "MAYBE": "❓ Maybe",
            "WAITLISTED": "⏳ Waitlisted",
            "PENDING": "🕐 Pending Approval",
            "NOT_GOING": "❌ Not Going",
        }
        for status, label in labels.items():
            entries = buckets[status]
            if entries:
                embed.add_field(
                    name=f"{label} ({len(entries)})",
                    value="\n".join(entries[:20]) + (f"\n*+{len(entries)-20} more*" if len(entries) > 20 else ""),
                    inline=False,
                )

        if not any(buckets.values()):
            embed.description = "*No RSVPs yet.*"

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /event start  (staff — manually start an event)
    # ------------------------------------------------------------------ #

    @event_group.command(name="start", description="Manually start a published event (spawns VC + attendance)")
    @app_commands.describe(codename="The event codename ID")
    @requires_django_perm("schedevents.can_manage_all_events")
    async def cmd_start(self, interaction: discord.Interaction, codename: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        plan = await self._get_plan_or_error(interaction, codename)
        if not plan:
            return

        if plan.status != self.EventPlan.Status.PUBLISHED:
            await interaction.followup.send(
                f"❌ Event must be PUBLISHED to start manually (current: {plan.get_status_display()}).",
                ephemeral=True,
            )
            return

        lifecycle: "EventLifecycleCog | None" = self.client.cogs.get("EventLifecycleCog")  # type: ignore
        if lifecycle:
            await lifecycle._start_event(plan)
            await interaction.followup.send(
                f"✅ Event **{plan.codename_id}** started.", ephemeral=True
            )
        else:
            # Lifecycle cog missing — just flip status manually
            plan.status = self.EventPlan.Status.ACTIVE
            plan._skip_signals = True
            await plan.asave(update_fields=["status"])
            await interaction.followup.send(
                f"✅ Status updated, but EventLifecycleCog not loaded — VC/thread may not have been created.", ephemeral=True
            )

    # ------------------------------------------------------------------ #
    # /event end  (staff — manually end an event)
    # ------------------------------------------------------------------ #

    @event_group.command(name="end", description="Manually end an active event (generates report)")
    @app_commands.describe(codename="The event codename ID")
    @requires_django_perm("schedevents.can_manage_all_events")
    async def cmd_end(self, interaction: discord.Interaction, codename: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        plan = await self._get_plan_or_error(interaction, codename)
        if not plan:
            return

        if plan.status != self.EventPlan.Status.ACTIVE:
            await interaction.followup.send(
                f"❌ Event is not ACTIVE (current: {plan.get_status_display()}).",
                ephemeral=True,
            )
            return

        plan.status = self.EventPlan.Status.COMPLETED
        await plan.asave(update_fields=["status"])

        await interaction.followup.send(
            f"✅ Event **{plan.codename_id}** ended. Report will be generated shortly.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _get_plan_or_error(self, interaction, codename, ephemeral=True):
        try:
            return await self.EventPlan.objects.select_related("indicator", "vc_profile").aget(
                codename_id=codename, guild_id=interaction.guild.id
            )
        except self.EventPlan.DoesNotExist:
            msg = f"❌ Event `{codename}` not found."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return None

    async def _check_edit_perm(self, interaction: discord.Interaction, plan) -> bool:
        """Returns True if the user may edit/cancel this event."""
        from app.unifieduser.models import OrgPlayer
        try:
            org_user = await OrgPlayer.objects.aget(discorduser__discorduid=interaction.user.id)
        except OrgPlayer.DoesNotExist:
            org_user = None

        has_global = await interaction.client.loop.run_in_executor(
            None,
            lambda: org_user.has_perm("schedevents.can_manage_all_events") if org_user else False,
        )
        if has_global:
            return True

        is_creator = org_user and plan.created_by_id == org_user.pk
        is_organizer = org_user and await plan.organizers.filter(pk=org_user.pk).aexists()

        if not (is_creator or is_organizer):
            msg = "❌ You don't have permission to modify this event."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return False
        return True


async def setup(client):
    await client.add_cog(EventManagerCog(client))
