"""
app/goals/cogs/goals_cog.py

Listens for OrgGoal/LeaderGoal Redis events and handles Discord thread creation,
status updates, and goal reminders.

Commands:
  /goal org set <title> [description] [due]    — create an org goal
  /goal org complete <goal_id>                 — mark org goal completed
  /goal leader set <member> <title> [due]      — create a leader goal
  /goal leader complete <goal_id>              — mark leader goal completed
"""
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.main.util.discord_command_checks import requires_django_perm

log = logging.getLogger(__name__)

GOALS_PUBSUB   = "app.goals.notify"
_ORG_PERM      = "goals.manage_org_goals"
_LEADER_PERM   = "goals.manage_leader_goals"


def _goal_embed(title: str, description: str, due: str | None, status: str, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or "", color=color)
    embed.add_field(name="Status", value=status, inline=True)
    if due:
        embed.add_field(name="Due", value=due, inline=True)
    return embed


_STATUS_COLORS = {
    "OPEN": 0x5865F2, "IN_PROGRESS": 0xFFC107,
    "COMPLETED": 0x22C55E, "CANCELLED": 0x6B7280,
}


class GoalsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot         = bot
        self.redis_client = None
        self.pubsub      = None

    async def cog_load(self):
        from django.conf import settings
        import redis.asyncio as redis

        pool              = redis.ConnectionPool.from_url(settings.CACHES["default"]["LOCATION"])
        self.redis_client = redis.Redis.from_pool(pool)
        self.pubsub       = self.redis_client.pubsub()
        await self.pubsub.subscribe(GOALS_PUBSUB)
        self.listener_loop.start()

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
                if action == "ORG_GOAL_CREATED":
                    await self._on_org_goal_created(data)
                elif action == "LEADER_GOAL_CREATED":
                    await self._on_leader_goal_created(data)
                elif action in ("ORG_GOAL_CLOSED", "LEADER_GOAL_CLOSED"):
                    await self._on_goal_closed(data)
                elif action == "GOAL_REMINDER_DUE":
                    await self._on_reminder_due(data)
        except Exception:
            log.exception("GoalsCog listener error:")

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    async def _on_org_goal_created(self, data: dict):
        from app.preferences.utils import aget_global_preference
        from app.goals.preferences import OrgGoalsChannelID, GoalPostToThreadEnabled

        if not await aget_global_preference(GoalPostToThreadEnabled.import_path):
            return

        chan_id = await aget_global_preference(OrgGoalsChannelID.import_path)
        if not chan_id:
            return

        channel = await self._get_channel(int(chan_id))
        if not channel:
            return

        embed = _goal_embed(
            title=data["title"],
            description=data.get("description", ""),
            due=data.get("due_date"),
            status="OPEN",
            color=_STATUS_COLORS["OPEN"],
        )
        embed.set_author(name="New Org Goal")

        msg = await channel.send(embed=embed)
        thread = await msg.create_thread(name=data["title"][:100], auto_archive_duration=10080)

        await self._save_thread_ids("goals.OrgGoal", data["goal_pk"], thread.id, msg.id)
        log.info("Org goal %s thread created: %s", data["goal_pk"], thread.id)

    async def _on_leader_goal_created(self, data: dict):
        from app.preferences.utils import aget_global_preference
        from app.goals.preferences import LeaderGoalsChannelID, GoalPostToThreadEnabled

        if not await aget_global_preference(GoalPostToThreadEnabled.import_path):
            return

        chan_id = await aget_global_preference(LeaderGoalsChannelID.import_path)
        if not chan_id:
            return

        channel = await self._get_channel(int(chan_id))
        if not channel:
            return

        discord_id = data.get("leader_discord_id")
        mention    = f"<@{discord_id}>" if discord_id else "Unlinked leader"

        embed = _goal_embed(
            title=data["title"],
            description=data.get("description", ""),
            due=data.get("due_date"),
            status="OPEN",
            color=_STATUS_COLORS["OPEN"],
        )
        embed.set_author(name=f"Leader Goal — {mention}")

        msg    = await channel.send(content=mention, embed=embed)
        thread = await msg.create_thread(name=data["title"][:100], auto_archive_duration=10080)

        await self._save_thread_ids("goals.LeaderGoal", data["goal_pk"], thread.id, msg.id)
        log.info("Leader goal %s thread created: %s", data["goal_pk"], thread.id)

    async def _on_goal_closed(self, data: dict):
        thread_id = data.get("thread_channel_id")
        msg_id    = data.get("thread_message_id")
        status    = data.get("status", "COMPLETED")
        if not thread_id:
            return

        thread = await self._get_channel(thread_id)
        if not thread:
            return

        icon = "✅" if status == "COMPLETED" else "🚫"
        await thread.send(f"{icon} **Goal {status.lower()}.**")
        try:
            await thread.edit(locked=True, archived=True)
        except (discord.HTTPException, discord.Forbidden):
            pass

    async def _on_reminder_due(self, data: dict):
        from app.preferences.utils import aget_global_preference
        from app.goals.preferences import GoalReminderDefaultChannelID

        chan_id = data.get("channel_id") or await aget_global_preference(GoalReminderDefaultChannelID.import_path)
        if not chan_id:
            chan_id = data.get("thread_channel_id")
        if not chan_id:
            return

        channel = await self._get_channel(int(chan_id))
        if not channel:
            return

        await channel.send(data.get("message", "⏰ Goal reminder."))

    # ------------------------------------------------------------------ #
    # Slash commands
    # ------------------------------------------------------------------ #

    goal = app_commands.Group(name="goal", description="Org and leader goal management")
    org_grp    = app_commands.Group(name="org",    description="Org-wide goals",   parent=goal)
    leader_grp = app_commands.Group(name="leader", description="Per-leader goals", parent=goal)

    @org_grp.command(name="set", description="Create a new org-wide goal")
    @app_commands.describe(title="Goal title", description="Detailed description", due="Due date (YYYY-MM-DD, optional)")
    @requires_django_perm(_ORG_PERM)
    async def org_goal_set(self, interaction: discord.Interaction, title: str, description: str = "", due: str = ""):
        from app.goals.models import OrgGoal
        from app.unifieduser.models import OrgPlayer
        from django.utils.dateparse import parse_datetime

        await interaction.response.defer(ephemeral=True)

        author = await OrgPlayer.objects.filter(discorduser__discorduid=interaction.user.id).afirst()
        due_dt = None
        if due:
            due_dt = parse_datetime(due + "T00:00:00Z")

        goal = await OrgGoal.objects.acreate(title=title, description=description, due_date=due_dt, created_by=author)
        await interaction.followup.send(f"✅ Org goal **{goal.title}** created (#{goal.pk}).", ephemeral=True)

    @org_grp.command(name="complete", description="Mark an org goal as completed")
    @app_commands.describe(goal_id="Goal PK (shown in /goal org list)")
    @requires_django_perm(_ORG_PERM)
    async def org_goal_complete(self, interaction: discord.Interaction, goal_id: int):
        from app.goals.models import OrgGoal
        from django.utils import timezone as tz

        await interaction.response.defer(ephemeral=True)
        obj = await OrgGoal.objects.filter(pk=goal_id).afirst()
        if not obj:
            await interaction.followup.send(f"Goal #{goal_id} not found.", ephemeral=True)
            return
        obj.status       = OrgGoal.Status.COMPLETED
        obj.completed_at = tz.now()
        await obj.asave(update_fields=["status", "completed_at"])
        await interaction.followup.send(f"✅ Org goal **{obj.title}** marked completed.", ephemeral=True)

    @leader_grp.command(name="set", description="Create a new leader goal")
    @app_commands.describe(member="Leader to assign the goal to", title="Goal title", due="Due date (YYYY-MM-DD, optional)")
    @requires_django_perm(_LEADER_PERM)
    async def leader_goal_set(self, interaction: discord.Interaction, member: discord.Member, title: str, due: str = ""):
        from app.goals.models import LeaderGoal
        from app.unifieduser.models import OrgPlayer
        from django.utils.dateparse import parse_datetime

        await interaction.response.defer(ephemeral=True)

        author = await OrgPlayer.objects.filter(discorduser__discorduid=interaction.user.id).afirst()
        leader = await OrgPlayer.objects.filter(discorduser__discorduid=member.id).afirst()
        if not leader:
            await interaction.followup.send(f"{member.mention} doesn't have a linked org profile.", ephemeral=True)
            return

        due_dt = None
        if due:
            due_dt = parse_datetime(due + "T00:00:00Z")

        goal = await LeaderGoal.objects.acreate(
            title=title, leader=leader, created_by=author, due_date=due_dt,
        )
        await interaction.followup.send(f"✅ Leader goal **{goal.title}** assigned to {member.mention} (#{goal.pk}).", ephemeral=True)

    @leader_grp.command(name="complete", description="Mark a leader goal as completed")
    @app_commands.describe(goal_id="Goal PK")
    @requires_django_perm(_LEADER_PERM)
    async def leader_goal_complete(self, interaction: discord.Interaction, goal_id: int):
        from app.goals.models import LeaderGoal
        from django.utils import timezone as tz

        await interaction.response.defer(ephemeral=True)
        obj = await LeaderGoal.objects.filter(pk=goal_id).afirst()
        if not obj:
            await interaction.followup.send(f"Goal #{goal_id} not found.", ephemeral=True)
            return
        obj.status       = LeaderGoal.Status.COMPLETED
        obj.completed_at = tz.now()
        await obj.asave(update_fields=["status", "completed_at"])
        await interaction.followup.send(f"✅ Leader goal **{obj.title}** marked completed.", ephemeral=True)

    @app_commands.command(name="goals", description="View open org goals")
    async def cmd_view_goals(self, interaction: discord.Interaction):
        """Public command — any member can see current open/in-progress org goals."""
        await interaction.response.defer(ephemeral=True)
        from app.goals.models import OrgGoal
        from asgiref.sync import sync_to_async

        @sync_to_async
        def _get_goals():
            return list(
                OrgGoal.objects.filter(
                    status__in=[OrgGoal.Status.OPEN, OrgGoal.Status.IN_PROGRESS]
                ).order_by("due_date", "-created_at")[:10]
            )

        goals = await _get_goals()
        if not goals:
            await interaction.followup.send("No open goals at the moment.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📋 Org Goals",
            description="Current open and in-progress goals for BlightVeil:",
            color=0x5865F2,
        )
        for g in goals:
            due_str = f" · Due <t:{int(g.due_date.timestamp())}:R>" if g.due_date else ""
            status_emoji = {"OPEN": "🔵", "IN_PROGRESS": "🟡"}.get(g.status, "⚪")
            embed.add_field(
                name=f"{status_emoji} {g.title}",
                value=(g.description[:100] + "…" if len(g.description) > 100 else g.description or "—") + due_str,
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        from app.main.util.discord_command_checks import MissingDjangoPermission, AccountNotLinked
        if isinstance(error, MissingDjangoPermission):
            msg = f"🛡️ **Permission denied:** you need `{error.missing_perm}`."
        elif isinstance(error, AccountNotLinked):
            msg = "🎮 Your Discord account isn't linked to an org profile yet."
        else:
            msg = "❌ An unexpected error occurred."
            log.exception("GoalsCog command error: %s", error)
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _get_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                log.warning("GoalsCog: channel %s not found", channel_id)
                return None
        return channel

    @staticmethod
    async def _save_thread_ids(model_label: str, pk, thread_id: int, msg_id: int):
        from asgiref.sync import sync_to_async
        from django.apps import apps

        def _update():
            app_label, model_name = model_label.split(".")
            Model = apps.get_model(app_label, model_name)
            Model.objects.filter(pk=pk).update(thread_channel_id=thread_id, thread_message_id=msg_id)

        await sync_to_async(_update)()


async def setup(bot: commands.Bot):
    await bot.add_cog(GoalsCog(bot))
