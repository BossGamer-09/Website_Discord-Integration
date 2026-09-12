import logging
import zoneinfo

import discord
from discord import app_commands
from discord.ext import commands
from asgiref.sync import sync_to_async
from django.apps import apps

from app.main.util.discord_command_checks import requires_django_perm

logger = logging.getLogger(__name__)

_PAGE_SIZE = 15

_COMMON_TIMEZONES = [
    "UTC", "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Vancouver", "America/Toronto",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
    "Asia/Tokyo", "Asia/Seoul", "Asia/Singapore", "Asia/Dubai",
    "Australia/Sydney", "Pacific/Auckland",
]


def _tz_display(tz_str: str) -> str:
    if not tz_str:
        return "Not set"
    try:
        import datetime
        tz = zoneinfo.ZoneInfo(tz_str)
        now = datetime.datetime.now(tz)
        offset = now.strftime("%z")
        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else f"UTC{offset}"
        return f"{tz_str} ({offset_fmt})"
    except Exception:
        return tz_str


class RosterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    roster_group = app_commands.Group(name="roster", description="Member roster commands")

    # -----------------------------------------------------------------------
    # /roster timezone
    # -----------------------------------------------------------------------

    @roster_group.command(name="timezone", description="Set your timezone so staff can see your local time on the roster")
    @app_commands.describe(timezone="IANA timezone name, e.g. America/New_York, Europe/London, UTC")
    async def set_timezone(self, interaction: discord.Interaction, timezone: str):
        await interaction.response.defer(ephemeral=True)

        try:
            zoneinfo.ZoneInfo(timezone)
        except (zoneinfo.ZoneInfoNotFoundError, KeyError):
            await interaction.followup.send(
                f"❌ `{timezone}` is not a valid IANA timezone.\n"
                "Examples: `America/New_York`, `Europe/London`, `Asia/Tokyo`, `UTC`",
                ephemeral=True,
            )
            return

        from app.discordauth.models import DiscordUser

        def _save():
            try:
                du = DiscordUser.objects.select_related("user").get(discorduid=interaction.user.id)
                if du.user:
                    du.user.timezone_str = timezone
                    du.user.save(update_fields=["timezone_str"])
                    return True
            except DiscordUser.DoesNotExist:
                pass
            return False

        saved = await sync_to_async(_save)()
        if saved:
            await interaction.followup.send(f"✅ Timezone set to **{_tz_display(timezone)}**.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Could not find your member record.", ephemeral=True)

    @set_timezone.autocomplete("timezone")
    async def timezone_autocomplete(self, interaction: discord.Interaction, current: str):
        matches = [tz for tz in _COMMON_TIMEZONES if current.lower() in tz.lower()][:25]
        return [app_commands.Choice(name=tz, value=tz) for tz in matches]

    # -----------------------------------------------------------------------
    # /roster list
    # -----------------------------------------------------------------------

    @roster_group.command(name="list", description="[Staff] List members with optional filters")
    @app_commands.describe(
        rank="Filter by rank name (partial match)",
        discipline="Filter by discipline name (partial match)",
        page="Page number (default 1)",
    )
    @requires_django_perm("unifieduser.view_orgplayer")
    async def roster_list(
        self,
        interaction: discord.Interaction,
        rank: str = "",
        discipline: str = "",
        page: int = 1,
    ):
        await interaction.response.defer(ephemeral=True)

        OrgPlayer = apps.get_model("unifieduser", "OrgPlayer")

        def _query():
            qs = OrgPlayer.objects.filter(
                is_active=True,
            ).select_related("rank", "discorduser").prefetch_related(
                "membership_applications__primary_focus"
            ).exclude(username="AnonymousUser")

            if rank:
                qs = qs.filter(rank__name__icontains=rank)

            if discipline:
                qs = qs.filter(
                    membership_applications__primary_focus__name__icontains=discipline,
                    membership_applications__status="APPROVED",
                ).distinct()

            return list(qs.order_by("rank__order", "username"))

        members = await sync_to_async(_query)()

        total = len(members)
        page = max(1, page)
        start = (page - 1) * _PAGE_SIZE
        page_members = members[start:start + _PAGE_SIZE]
        total_pages = max(1, -(-total // _PAGE_SIZE))

        if not page_members:
            await interaction.followup.send("No members found matching those filters.", ephemeral=True)
            return

        lines = []
        for m in page_members:
            rank_str = f"{m.rank.prefix} {m.rank.name}".strip() if m.rank else "No Rank"
            disc_uid = getattr(getattr(m, "discorduser", None), "discorduid", None)
            mention  = f"<@{disc_uid}>" if disc_uid else m.display_name
            tz_str   = f" `{m.timezone_str}`" if m.timezone_str else ""
            lines.append(f"**{rank_str}** — {mention}{tz_str}")

        embed = discord.Embed(
            title=f"Member Roster — Page {page}/{total_pages} ({total} total)",
            description="\n".join(lines),
            color=0x5865F2,
        )
        filters = []
        if rank:
            filters.append(f"Rank: `{rank}`")
        if discipline:
            filters.append(f"Discipline: `{discipline}`")
        if filters:
            embed.set_footer(text="Filters: " + " · ".join(filters))

        await interaction.followup.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /roster member
    # -----------------------------------------------------------------------

    @roster_group.command(name="member", description="[Staff] View detailed profile for a member")
    @app_commands.describe(member="The Discord member to look up")
    @requires_django_perm("unifieduser.view_orgplayer")
    async def roster_member(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

        from app.discordauth.models import DiscordUser

        def _fetch():
            try:
                du = DiscordUser.objects.select_related(
                    "user__rank",
                    "user__displaynamesearchcache",
                ).get(discorduid=member.id)
                player = du.user
                if not player:
                    return None, None, None

                app = player.membership_applications.filter(
                    status="APPROVED"
                ).select_related(
                    "primary_focus",
                    "referred_by__displaynamesearchcache",
                    "referred_by__discorduser",
                    "reviewer__displaynamesearchcache",
                    "reviewer__discorduser",
                ).order_by("-reviewed_at").first()

                def _dn(p):
                    if not p:
                        return None
                    try:
                        return p.displaynamesearchcache.display_name
                    except Exception:
                        du2 = getattr(p, 'discorduser', None)
                        return getattr(du2, 'main_guild_nick', None) or p.username

                app_data = None
                if app:
                    app_data = {
                        "disc_str":    app.primary_focus.name if app.primary_focus else "—",
                        "referrer":    _dn(app.referred_by) or (app.referral_note or "—"),
                        "approved_by": _dn(app.reviewer) or "—",
                        "reviewed_at": app.reviewed_at,
                        "rsi_handle":  app.rsi_handle,
                    }

                try:
                    display_name = player.displaynamesearchcache.display_name
                except Exception:
                    display_name = du.main_guild_nick or player.username

                return player, app_data, display_name
            except DiscordUser.DoesNotExist:
                return None, None, None

        player, app_data, display_name = await sync_to_async(_fetch)()

        if not player:
            await interaction.followup.send(f"No backend record found for {member.mention}.", ephemeral=True)
            return

        def _event_stats():
            from app.schedevents.models import EventRSVP
            qs = EventRSVP.objects.filter(user=player)
            attended   = qs.filter(checked_in=True).count()
            late_count = qs.filter(attendance_outcome=EventRSVP.AttendanceOutcome.LATE).count()
            absent     = qs.filter(attendance_outcome=EventRSVP.AttendanceOutcome.ABSENT).count()
            return attended, late_count, absent

        attended, late_count, absent = await sync_to_async(_event_stats)()

        rank_str = f"{player.rank.prefix} {player.rank.name}".strip() if player.rank else "No Rank"

        embed = discord.Embed(title=f"🪪 {display_name}", color=0x5865F2)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Rank",           value=rank_str,                              inline=True)
        embed.add_field(name="Timezone",       value=_tz_display(player.timezone_str),      inline=True)
        embed.add_field(name="Discord",        value=member.mention,                        inline=True)
        embed.add_field(name="Joined Discord", value=discord.utils.format_dt(member.joined_at, "D") if member.joined_at else "Unknown", inline=True)
        embed.add_field(name="First Event",    value=discord.utils.format_dt(player.first_event_at, "D") if player.first_event_at else "None", inline=True)
        embed.add_field(name="Events",         value=f"✅ {attended} attended · 🕐 {late_count} late · ❌ {absent} no-show", inline=False)

        if app_data:
            embed.add_field(name="Discipline",   value=app_data["disc_str"],    inline=True)
            embed.add_field(name="Referred By",  value=app_data["referrer"],    inline=True)
            embed.add_field(name="Approved By",  value=app_data["approved_by"], inline=True)
            if app_data["reviewed_at"]:
                embed.add_field(name="Approved", value=discord.utils.format_dt(app_data["reviewed_at"], "D"), inline=True)
            if app_data["rsi_handle"]:
                embed.add_field(name="RSI Handle", value=app_data["rsi_handle"], inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /roster events
    # -----------------------------------------------------------------------

    @roster_group.command(name="events", description="[Staff] View a member's event attendance history")
    @app_commands.describe(member="The Discord member to look up", page="Page number (default 1)")
    @requires_django_perm("unifieduser.view_orgplayer")
    async def roster_events(self, interaction: discord.Interaction, member: discord.Member, page: int = 1):
        await interaction.response.defer(ephemeral=True)

        from app.discordauth.models import DiscordUser

        def _fetch():
            try:
                du = DiscordUser.objects.select_related("user").get(discorduid=member.id)
            except DiscordUser.DoesNotExist:
                return None, []

            player = du.user
            if not player:
                return None, []

            from app.schedevents.models import EventRSVP, EventPlan
            rsvps = list(
                EventRSVP.objects.filter(user=player)
                .select_related("event")
                .order_by("-event__planned_start_at")
                [(page - 1) * _PAGE_SIZE : page * _PAGE_SIZE + 1]
            )
            total = EventRSVP.objects.filter(user=player).count()
            return player, rsvps, total

        result = await sync_to_async(_fetch)()
        player, rsvps, total = result

        if not player:
            await interaction.followup.send(f"No backend record for {member.mention}.", ephemeral=True)
            return

        if not rsvps:
            await interaction.followup.send(f"{member.mention} has no event history.", ephemeral=True)
            return

        from app.schedevents.models import EventRSVP
        outcome_emoji = {
            EventRSVP.AttendanceOutcome.PRESENT:    "✅",
            EventRSVP.AttendanceOutcome.LATE:       "🕐",
            EventRSVP.AttendanceOutcome.LEFT_EARLY: "🚪",
            EventRSVP.AttendanceOutcome.ABSENT:     "❌",
            None: "—",
        }

        page_rsvps = rsvps[:_PAGE_SIZE]
        has_more = len(rsvps) > _PAGE_SIZE
        total_pages = max(1, -(-total // _PAGE_SIZE))

        lines = []
        for r in page_rsvps:
            ts = discord.utils.format_dt(r.event.planned_start_at, "d")
            outcome = outcome_emoji.get(r.attendance_outcome, "—")
            late_str = ""
            if r.attendance_outcome == EventRSVP.AttendanceOutcome.LATE and r.check_in_time and r.event.planned_start_at:
                late_min = int((r.check_in_time - r.event.planned_start_at).total_seconds() // 60)
                late_str = f" (+{late_min}m)"
            lines.append(f"{outcome}{late_str} **{r.event.title[:40]}** {ts}")

        # Summary stats across all events (not just this page)
        def _stats():
            from app.schedevents.models import EventRSVP as RSVP
            qs = RSVP.objects.filter(user=player, checked_in=True)
            attended   = qs.count()
            late_count = qs.filter(attendance_outcome=RSVP.AttendanceOutcome.LATE).count()
            left_early = qs.filter(attendance_outcome=RSVP.AttendanceOutcome.LEFT_EARLY).count()
            absent     = RSVP.objects.filter(user=player, attendance_outcome=RSVP.AttendanceOutcome.ABSENT).count()
            return attended, late_count, left_early, absent

        attended, late_count, left_early, absent = await sync_to_async(_stats)()

        embed = discord.Embed(
            title=f"📋 Event History — {member.display_name}",
            description="\n".join(lines),
            color=0x5865F2,
        )
        embed.add_field(name="Attended",   value=str(attended),   inline=True)
        embed.add_field(name="Late",       value=str(late_count), inline=True)
        embed.add_field(name="Left Early", value=str(left_early), inline=True)
        embed.add_field(name="No-show",    value=str(absent),     inline=True)
        if player.first_event_at:
            embed.add_field(name="First Event", value=discord.utils.format_dt(player.first_event_at, "D"), inline=True)
        embed.set_footer(text=f"Page {page}/{total_pages} · {total} total RSVPs")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /roster referrals
    # -----------------------------------------------------------------------

    @roster_group.command(name="referrals", description="[Staff] Show all referrals made by a member")
    @app_commands.describe(member="The member whose referrals to list")
    @requires_django_perm("unifieduser.view_orgplayer")
    async def roster_referrals(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

        from app.discordauth.models import DiscordUser
        from app.candidacy.models import MembershipApplicationRecord

        def _fetch():
            try:
                du = DiscordUser.objects.select_related(
                    "user__displaynamesearchcache",
                ).get(discorduid=member.id)
                player = du.user
                if not player:
                    return None, None, []
                try:
                    display_name = player.displaynamesearchcache.display_name
                except Exception:
                    display_name = du.main_guild_nick or player.username
                refs = list(
                    MembershipApplicationRecord.objects.filter(
                        referred_by=player
                    ).select_related(
                        "applicant__displaynamesearchcache",
                        "applicant__discorduser",
                    ).order_by("-submitted_at")[:25]
                )
                return player, display_name, refs
            except DiscordUser.DoesNotExist:
                return None, None, []

        player, display_name, refs = await sync_to_async(_fetch)()

        if not player:
            await interaction.followup.send(f"No backend record for {member.mention}.", ephemeral=True)
            return

        if not refs:
            await interaction.followup.send(f"{member.mention} has no recorded referrals.", ephemeral=True)
            return

        lines = []
        for r in refs:
            try:
                ref_player = r.applicant
                try:
                    who = ref_player.displaynamesearchcache.display_name
                except Exception:
                    du2 = getattr(ref_player, 'discorduser', None)
                    who = getattr(du2, 'main_guild_nick', None) or ref_player.username
            except Exception:
                who = "—"
            status_emoji = {"APPROVED": "✅", "DENIED": "❌", "PENDING": "⏳"}.get(r.status, "❓")
            lines.append(f"{status_emoji} {who} — {r.submitted_at.strftime('%Y-%m-%d')}")

        embed = discord.Embed(
            title=f"Referrals by {display_name} ({len(refs)})",
            description="\n".join(lines),
            color=0x22C55E,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(RosterCog(bot))
