"""
app/schedevents/cogs/event_listing.py

/events command — Sesh.fyi-style event listing embed.

Displays upcoming PUBLISHED/ACTIVE events grouped by date, respecting
EventRoleRestriction visibility rules. Supports time-format toggle and
live-list posting.
"""
import logging
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands
from django.apps import apps
from django.utils import timezone

from app.preferences.utils import aget_global_preference
from app.schedevents import preferences as prefs
from app.main.util.discord_command_checks import requires_django_perm

log = logging.getLogger(__name__)

_MONTHS_AHEAD = 3   # how many months of events to load
_MAX_EVENTS   = 50  # hard cap per listing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tz_offset_str(tz: ZoneInfo) -> str:
    offset = datetime.now(tz).utcoffset()
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    h, m = divmod(abs(total_minutes), 60)
    return f"{sign}{h:02d}:{m:02d}"


def _rel_str(delta_days: int) -> str:
    if delta_days == 0:
        return "today"
    if delta_days == 1:
        return "tomorrow"
    return f"in {delta_days} days"


def _build_listing_embed(
    event_rows: list,          # list of (EventPlan, list[EventRoleRestriction])
    tz: ZoneInfo,
    use_discord_ts: bool,
) -> discord.Embed:
    """
    Build the Sesh-style listing embed.
    event_rows is already filtered to what the calling member may see.
    """
    tz_offset = _tz_offset_str(tz)
    header = (
        "Times shown in your local timezone"
        if use_discord_ts
        else f"Times displayed in server timezone (UTC {tz_offset})"
    )

    embed = discord.Embed(title="☐ Event Listings", color=0x2B2D31)

    if not event_rows:
        embed.description = f"{header}\n\n*No upcoming events.*"
        embed.set_footer(text="Showing events visible to list creator")
        return embed

    today = datetime.now(tz).date()
    grouped: dict[str, list] = defaultdict(list)
    group_order: list[tuple[str, str]] = []

    for plan, restrictions in event_rows:
        local_dt = plan.planned_start_at.astimezone(tz)
        local_date = local_dt.date()
        delta = (local_date - today).days

        if delta <= 14:
            key   = f"day:{local_date.isoformat()}"
            month_abbr = local_dt.strftime("%b")
            label = f"**{local_dt.strftime('%A')} [{month_abbr} {local_dt.day}]**"
        else:
            key   = f"month:{local_dt.year}-{local_dt.month:02d}"
            label = f"**{local_dt.strftime('%B')}**"

        if key not in grouped:
            group_order.append((key, label))
        grouped[key].append((plan, restrictions, local_dt, delta))

    lines = [header, ""]

    for key, label in group_order:
        lines.append(label)
        for plan, restrictions, local_dt, delta in grouped[key]:
            if use_discord_ts:
                ts = int(plan.planned_start_at.timestamp())
                time_part = f"<t:{ts}:t>"
                rel_part  = f"<t:{ts}:R>"
            else:
                raw = local_dt.strftime("%I:%M%p").lstrip("0").lower()
                time_part = raw
                rel_part  = _rel_str(delta)

            # For month-level groups prepend the short date
            if key.startswith("month:") and not use_discord_ts:
                time_part = f"{local_dt.strftime('%b')} {local_dt.day} {time_part}"

            # Indicator prefix + name in bold
            name_part = f"{plan.indicator.prefix} **{plan.title}**"

            # Role restriction note
            if restrictions:
                mentions = ", ".join(f"<@&{r.role_id}>" for r in restrictions)
                restr_part = f"\n> *Limited to: {mentions}*"
            else:
                restr_part = ""

            lines.append(f"│ {time_part}  {name_part}  *{rel_part}*{restr_part}")
        lines.append("")

    lines.append("*Showing events visible to list creator*")
    embed.description = "\n".join(lines)
    return embed


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

class EventListingView(discord.ui.View):
    def __init__(self, cog: "EventListingCog", guild: discord.Guild,
                 member: discord.Member, tz: ZoneInfo, use_discord_ts: bool = False):
        super().__init__(timeout=300)
        self.cog          = cog
        self.guild        = guild
        self.member       = member
        self.tz           = tz
        self.use_discord_ts = use_discord_ts
        self._refresh_buttons()

    def _refresh_buttons(self):
        self.convert_btn.style = (
            discord.ButtonStyle.success if self.use_discord_ts
            else discord.ButtonStyle.secondary
        )
        self.convert_btn.label = (
            "● Convert time" if self.use_discord_ts else "○ Convert time"
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                "This listing belongs to someone else.", ephemeral=True
            )
            return False
        return True

    # -- Convert time toggle --------------------------------------------------

    @discord.ui.button(label="○ Convert time", style=discord.ButtonStyle.secondary,
                       custom_id="evtlist_convert")
    async def convert_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.use_discord_ts = not self.use_discord_ts
        self._refresh_buttons()
        rows  = await self.cog._fetch_visible_rows(self.guild, self.member)
        embed = await sync_to_async(_build_listing_embed)(rows, self.tz, self.use_discord_ts)
        await interaction.response.edit_message(embed=embed, view=self)

    # -- Activate live list ---------------------------------------------------

    @discord.ui.button(label="□ Activate live list", style=discord.ButtonStyle.secondary,
                       custom_id="evtlist_live")
    async def live_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows  = await self.cog._fetch_visible_rows(self.guild, self.member)
        embed = await sync_to_async(_build_listing_embed)(rows, self.tz, self.use_discord_ts)
        embed.set_footer(text=f"Live list  •  Last updated: {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message(
            "✅ Live listing posted in this channel.", ephemeral=True
        )

    # -- Refresh --------------------------------------------------------------

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.secondary,
                       custom_id="evtlist_refresh")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows  = await self.cog._fetch_visible_rows(self.guild, self.member)
        embed = await sync_to_async(_build_listing_embed)(rows, self.tz, self.use_discord_ts)
        await interaction.response.edit_message(embed=embed, view=self)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class EventListingCog(commands.Cog):
    def __init__(self, client):
        self.client       = client
        self.EventPlan    = apps.get_model("schedevents", "EventPlan")
        self.EventRoleRestriction = apps.get_model("schedevents", "EventRoleRestriction")

    # ------------------------------------------------------------------ #
    # /events
    # ------------------------------------------------------------------ #

    @app_commands.command(name="events", description="Browse upcoming org events")
    @requires_django_perm("schedevents.view_eventplan")
    async def cmd_events(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        tz_name = await aget_global_preference(prefs.EventListingTimezone.import_path) or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, Exception):
            tz = ZoneInfo("UTC")

        rows  = await self._fetch_visible_rows(interaction.guild, interaction.user)
        embed = await sync_to_async(_build_listing_embed)(rows, tz, use_discord_ts=False)
        view  = EventListingView(self, interaction.guild, interaction.user, tz)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------ #
    # Shared data fetch
    # ------------------------------------------------------------------ #

    async def _fetch_visible_rows(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> list:
        """
        Returns list of (EventPlan, [EventRoleRestriction]) for events the
        member is allowed to see, ordered by planned_start_at.
        """
        now = timezone.now()
        plans = [
            p async for p in self.EventPlan.objects.filter(
                status__in=[
                    self.EventPlan.Status.PUBLISHED,
                    self.EventPlan.Status.ACTIVE,
                ],
                planned_start_at__gte=now,
                guild_id=guild.id,
            )
            .select_related("indicator")
            .order_by("planned_start_at")[:_MAX_EVENTS]
        ]

        # Bulk-fetch all restrictions for these events
        plan_ids = [p.codename_id for p in plans]
        all_restrictions = [
            r async for r in self.EventRoleRestriction.objects.filter(
                event_id__in=plan_ids,
                rsvp_option__isnull=True,   # visibility-level restrictions only
            )
        ]

        restr_map: dict[str, list] = defaultdict(list)
        for r in all_restrictions:
            restr_map[r.event_id].append(r)

        # member.roles is available when guild cache is populated; fall back to role IDs
        member_role_ids: set[int] = {role.id for role in getattr(member, "roles", [])}

        rows = []
        for plan in plans:
            restrictions = restr_map.get(plan.codename_id, [])
            if restrictions:
                allowed_role_ids = {r.role_id for r in restrictions}
                if not (member_role_ids & allowed_role_ids):
                    continue  # member can't see this event
            rows.append((plan, restrictions))

        return rows


async def setup(client):
    await client.add_cog(EventListingCog(client))
