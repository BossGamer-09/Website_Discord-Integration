"""
app/candidacy/cogs/applicant_selfservice.py

Self-service slash commands for applicants / unverified guild members.
No requires_django_perm — available to anyone in the guild who has a linked OrgPlayer.
"""
import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands
from django.utils import timezone

log = logging.getLogger(__name__)

_RATE_LIMIT_PREFIX = "candidacy:help_request:ratelimit:"
_RATE_LIMIT_TTL    = 86400  # 1 day in seconds


async def _get_redis(settings=None):
    import redis.asyncio as redis
    from django.conf import settings as _settings
    pool = redis.ConnectionPool.from_url(_settings.CACHES["default"]["LOCATION"])
    return redis.Redis.from_pool(pool)


async def _resolve_org_player(discord_uid: int):
    """Return OrgPlayer or None if not linked."""
    from app.discordauth.models import DiscordUser
    try:
        du = await DiscordUser.objects.select_related("user").aget(discorduid=discord_uid)
        return du.user
    except DiscordUser.DoesNotExist:
        return None


async def _player_allowed(player) -> bool:
    """
    Return True if the player is allowed to use self-service commands.

    When ApplicantSelfServiceGroups is blank, anyone with a linked OrgPlayer passes.
    When set, the player must belong to at least one of the configured groups.
    """
    from app.preferences.utils import aget_global_preference
    from app.candidacy.preferences import ApplicantSelfServiceGroups
    from asgiref.sync import sync_to_async

    raw = await aget_global_preference(ApplicantSelfServiceGroups.import_path)
    if not raw or not raw.strip():
        return True

    try:
        allowed_ids = {int(pk.strip()) for pk in raw.split(",") if pk.strip()}
    except ValueError:
        return True

    @sync_to_async
    def _check():
        return player.groups.filter(id__in=allowed_ids).exists()

    return await _check()


class ApplicantSelfServiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------ #
    # /my_application
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="my_application",
        description="Check the status of your membership application.",
    )
    async def cmd_my_application(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        player = await _resolve_org_player(interaction.user.id)
        if not player:
            await interaction.followup.send(
                "❌ Your Discord account is not linked to a BlightVeil account. "
                "Please use `/link` or contact staff.",
                ephemeral=True,
            )
            return
        if not await _player_allowed(player):
            await interaction.followup.send("❌ You don't have permission to use this command.", ephemeral=True)
            return

        from app.candidacy.models import MembershipApplicationRecord
        record = await (
            MembershipApplicationRecord.objects
            .filter(applicant=player)
            .select_related("membership_type", "reviewer")
            .order_by("-submitted_at")
            .afirst()
        )

        if not record:
            await interaction.followup.send(
                "You don't have a membership application on file.\n"
                "Use `/apply_membership` to start one.",
                ephemeral=True,
            )
            return

        status_icons = {
            MembershipApplicationRecord.Status.PENDING:  "🕐",
            MembershipApplicationRecord.Status.APPROVED: "✅",
            MembershipApplicationRecord.Status.DENIED:   "❌",
            MembershipApplicationRecord.Status.RESET:    "🔄",
        }
        icon = status_icons.get(record.status, "❓")

        embed = discord.Embed(
            title="Your Membership Application",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Type",      value=record.membership_type.name if record.membership_type else "—", inline=True)
        embed.add_field(name="Status",    value=f"{icon} {record.get_status_display()}", inline=True)
        embed.add_field(
            name="Reviewer",
            value=record.reviewer.display_name if record.reviewer else "Not yet assigned",
            inline=True,
        )
        embed.add_field(
            name="Submitted",
            value=discord.utils.format_dt(record.submitted_at, style="R"),
            inline=True,
        )
        embed.set_footer(text="Contact a staff member if you have questions.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /reopen_application
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="reopen_application",
        description="Request to reopen a recently denied membership application.",
    )
    async def cmd_reopen_application(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        player = await _resolve_org_player(interaction.user.id)
        if not player:
            await interaction.followup.send(
                "❌ Your Discord account is not linked. Use `/link` or contact staff.",
                ephemeral=True,
            )
            return
        if not await _player_allowed(player):
            await interaction.followup.send("❌ You don't have permission to use this command.", ephemeral=True)
            return

        from app.candidacy.models import MembershipApplicationRecord
        from app.preferences.utils import aget_global_preference
        from app.candidacy.preferences import MembershipDenialCooldownDays, MembershipReviewChannelID

        record = await (
            MembershipApplicationRecord.objects
            .filter(applicant=player)
            .select_related("membership_type")
            .order_by("-submitted_at")
            .afirst()
        )

        if not record or record.status != MembershipApplicationRecord.Status.DENIED:
            await interaction.followup.send(
                "❌ You don't have a denied application to reopen.",
                ephemeral=True,
            )
            return

        cooldown_days = await aget_global_preference(MembershipDenialCooldownDays.import_path) or 30
        cutoff = record.reviewed_at or record.submitted_at
        if cutoff and (timezone.now() - cutoff) > timedelta(days=cooldown_days):
            await interaction.followup.send(
                f"❌ Your application was denied more than **{cooldown_days} days** ago and can no longer be reopened. "
                "Please start a new application with `/apply_membership`.",
                ephemeral=True,
            )
            return

        record.status = MembershipApplicationRecord.Status.PENDING
        await record.asave(update_fields=["status"])

        # Notify the staff review thread / channel
        chan_id = await aget_global_preference(MembershipReviewChannelID.import_path)
        if chan_id:
            channel = self.bot.get_channel(int(chan_id)) or await self.bot.fetch_channel(int(chan_id))
            if channel:
                thread = None
                if record.thread_id:
                    try:
                        thread = await self.bot.fetch_channel(record.thread_id)
                    except (discord.NotFound, discord.Forbidden):
                        thread = None

                target = thread or channel
                embed = discord.Embed(
                    title="🔄 Application Reopened",
                    description=(
                        f"{interaction.user.mention} ({interaction.user.display_name}) has requested to reopen "
                        f"their denied **{record.membership_type.name if record.membership_type else 'membership'}** application."
                    ),
                    color=discord.Color.orange(),
                )
                embed.set_footer(text=f"Record ID: {record.pk}")
                await target.send(embed=embed)

        await interaction.followup.send(
            "✅ Your application has been moved back to **Pending** status. "
            "Staff have been notified and will review it shortly.",
            ephemeral=True,
        )
        log.info("Application %s reopened by %s", record.pk, interaction.user)

    # ------------------------------------------------------------------ #
    # /request_help  (rate-limited 1/day via Redis)
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="request_help",
        description="Send a help message to the current onboarding leaders.",
    )
    @app_commands.describe(message="What do you need help with?")
    async def cmd_request_help(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True)

        player = await _resolve_org_player(interaction.user.id)
        if not player:
            await interaction.followup.send(
                "❌ Your Discord account is not linked. Use `/link` or contact staff.",
                ephemeral=True,
            )
            return
        if not await _player_allowed(player):
            await interaction.followup.send("❌ You don't have permission to use this command.", ephemeral=True)
            return

        # Redis rate limit: 1 per day per user
        redis = await _get_redis()
        rate_key = f"{_RATE_LIMIT_PREFIX}{interaction.user.id}"
        try:
            already = await redis.get(rate_key)
            if already:
                ttl = await redis.ttl(rate_key)
                hours   = ttl // 3600
                minutes = (ttl % 3600) // 60
                await interaction.followup.send(
                    f"⏳ You've already sent a help request today. "
                    f"You can send another in **{hours}h {minutes}m**.",
                    ephemeral=True,
                )
                return
            await redis.setex(rate_key, _RATE_LIMIT_TTL, "1")
        finally:
            await redis.aclose()

        # Fetch current onboarding leader assignment
        from app.candidacy.models import OnboardingLeaderAssignment
        assignment = await (
            OnboardingLeaderAssignment.objects
            .prefetch_related("leaders__discorduser")
            .order_by("-week_start")
            .afirst()
        )

        sent_count = 0
        if assignment:
            async for leader in assignment.leaders.all():
                try:
                    from app.discordauth.models import DiscordUser
                    du = await DiscordUser.objects.aget(user=leader)
                    discord_user = await self.bot.fetch_user(int(du.discorduid))
                    embed = discord.Embed(
                        title="🙋 Help Request from Applicant",
                        description=message,
                        color=discord.Color.yellow(),
                    )
                    embed.set_author(
                        name=f"{interaction.user.display_name} ({interaction.user})",
                        icon_url=interaction.user.display_avatar.url,
                    )
                    embed.set_footer(text=f"User ID: {interaction.user.id}")
                    await discord_user.send(embed=embed)
                    sent_count += 1
                except (discord.Forbidden, discord.NotFound, AttributeError, Exception) as e:
                    log.warning("Could not DM onboarding leader %s: %s", leader, e)

        if sent_count:
            await interaction.followup.send(
                f"✅ Your message has been sent to **{sent_count}** onboarding leader(s). "
                "Someone will get back to you soon!",
                ephemeral=True,
            )
        else:
            # Fallback: notify chamberlain channel
            from app.preferences.utils import aget_global_preference
            from app.candidacy.preferences import ChamberlainNotifyChannelID
            chan_id = await aget_global_preference(ChamberlainNotifyChannelID.import_path)
            channel = None
            if chan_id:
                try:
                    channel = self.bot.get_channel(int(chan_id)) or await self.bot.fetch_channel(int(chan_id))
                except (discord.NotFound, discord.Forbidden):
                    pass

            if channel:
                embed = discord.Embed(
                    title="🙋 Help Request (No Leaders Available)",
                    description=message,
                    color=discord.Color.yellow(),
                )
                embed.set_author(
                    name=f"{interaction.user.display_name} ({interaction.user})",
                    icon_url=interaction.user.display_avatar.url,
                )
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                await channel.send(embed=embed)
                await interaction.followup.send(
                    "✅ No onboarding leaders are currently assigned, but your request has been posted to the staff channel.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "⚠️ No onboarding leaders are currently assigned and no staff channel is configured. "
                    "Please contact a staff member directly.",
                    ephemeral=True,
                )

        log.info("Help request from %s (%s): %s", interaction.user, interaction.user.id, message[:80])

    # ------------------------------------------------------------------ #
    # /my_discipline
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="my_discipline",
        description="See your currently assigned discipline(s).",
    )
    async def cmd_my_discipline(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        player = await _resolve_org_player(interaction.user.id)
        if not player:
            await interaction.followup.send(
                "❌ Your Discord account is not linked. Use `/link` or contact staff.",
                ephemeral=True,
            )
            return
        if not await _player_allowed(player):
            await interaction.followup.send("❌ You don't have permission to use this command.", ephemeral=True)
            return

        from app.candidacy.models import AssignableDiscipline
        from asgiref.sync import sync_to_async

        @sync_to_async
        def _get_player_disciplines(player):
            player_group_ids = set(player.groups.values_list("id", flat=True))
            disciplines = list(
                AssignableDiscipline.objects
                .filter(is_active=True, permission_groups__in=player_group_ids)
                .distinct()
            )
            return disciplines

        disciplines = await _get_player_disciplines(player)

        if not disciplines:
            await interaction.followup.send(
                "You don't have any disciplines assigned yet.\n"
                "Use `/discipline_select` to choose one!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Your Disciplines",
            color=discord.Color.blurple(),
        )
        for d in disciplines:
            embed.add_field(name=d.name, value=d.description or "—", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ApplicantSelfServiceCog(bot))
