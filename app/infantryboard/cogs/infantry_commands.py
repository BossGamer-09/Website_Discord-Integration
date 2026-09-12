"""
app/infantryboard/cogs/infantry_commands.py

Discord slash commands for the Infantry Roster system.

Commands (all under /infantry group):
  /infantry view <soldier>                    — full stats card (view perm)
  /infantry add <member> [name]               — create soldier from a Discord member (manage perm)
  /infantry delete <soldier>                  — remove a soldier (manage perm)
  /infantry rate <soldier> <skill> <value>    — update skill score 0–5 (manage perm)
  /infantry tag <soldier> <tag> <add|remove>  — manage tags (manage perm)
  /infantry status <soldier> <active|inactive>— toggle active status (manage perm)
  /infantry goal set <soldier> <text>         — set new goal (manage perm)
  /infantry goal complete <soldier>           — mark current goal done (manage perm)
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.main.util.discord_command_checks import (
    AccountNotLinked,
    MissingDjangoPermission,
    requires_django_perm,
)
from app.infantryboard.models import InfantryTag

log = logging.getLogger(__name__)

_PII_WARNING = (
    "⚠️ **Personal Information Notice**\n\n"
    "The information you are about to view may include personal details about "
    "org members (display names, skill assessments, goals, and linked accounts).\n\n"
    "**This information is confidential.** Do not share, screenshot, or distribute "
    "it outside of authorised staff channels.\n\n"
    "By pressing **Confirm** you acknowledge this policy."
)

_VIEW_PERM   = "infantryboard.view_infantry_roster"
_MANAGE_PERM = "infantryboard.manage_infantry_roster"

_TAG_LABELS = {t.value: t.label for t in InfantryTag}
_SKILL_CHOICES = [
    app_commands.Choice(name="Aim",        value="aim"),
    app_commands.Choice(name="Teamplay",   value="teamplay"),
    app_commands.Choice(name="Comms",      value="comms"),
    app_commands.Choice(name="Tactics",    value="tactics"),
    app_commands.Choice(name="Leadership", value="leadership"),
]
_TAG_CHOICES = [app_commands.Choice(name=t.label, value=t.value) for t in InfantryTag]

_TIER = {
    0: "—",
    1: "Recruit",
    2: "Standard",
    3: "Proficient",
    4: "Expert",
    5: "Elite",
}
_SKULL_FILLED = "💀"
_SKULL_EMPTY  = "⬜"


def _skull_bar(n: int) -> str:
    return _SKULL_FILLED * n + _SKULL_EMPTY * (5 - n)


def _soldier_embed(soldier, open_goal=None) -> discord.Embed:
    embed = discord.Embed(title=soldier.name, color=0x7C3AED)
    if not soldier.is_active:
        embed.description = "🔴 **Inactive**"
    embed.add_field(
        name="Aim",
        value=f"{_skull_bar(soldier.aim)}\n{_TIER[soldier.aim]}",
        inline=True,
    )
    embed.add_field(
        name="Teamplay",
        value=f"{_skull_bar(soldier.teamplay)}\n{_TIER[soldier.teamplay]}",
        inline=True,
    )
    embed.add_field(
        name="Comms",
        value=f"{_skull_bar(soldier.comms)}\n{_TIER[soldier.comms]}",
        inline=True,
    )
    embed.add_field(
        name="Tactics",
        value=f"{_skull_bar(soldier.tactics)}\n{_TIER[soldier.tactics]}",
        inline=True,
    )
    embed.add_field(
        name="Leadership",
        value=f"{_skull_bar(soldier.leadership)}\n{_TIER[soldier.leadership]}",
        inline=True,
    )
    if open_goal:
        embed.add_field(name="Current Goal", value=f"*{open_goal.title}*", inline=False)
    if soldier.updated_by:
        embed.set_footer(text=f"Last updated by {soldier.updated_by.display_name}")
    return embed


# ---------------------------------------------------------------------------
# Shared autocomplete helper
# ---------------------------------------------------------------------------

async def _soldier_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    from app.infantryboard.models import Soldier
    choices = []
    qs = Soldier.objects.order_by("name")
    if current:
        qs = qs.filter(name__icontains=current)
    async for s in qs[:25]:
        choices.append(app_commands.Choice(name=s.name, value=s.name))
    return choices


# ---------------------------------------------------------------------------
# PII gate
# ---------------------------------------------------------------------------

class _PIIConfirmView(discord.ui.View):
    def __init__(self, callback, *cb_args, **cb_kwargs):
        super().__init__(timeout=60)
        self._cb        = callback
        self._cb_args   = cb_args
        self._cb_kwargs = cb_kwargs

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Loading…", view=None, embed=None)
        await self._cb(interaction, *self._cb_args, **self._cb_kwargs)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None, embed=None)

    async def on_timeout(self):
        pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class InfantryboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------------------------------------------------
    # /infantry group
    # -----------------------------------------------------------------------

    infantry = app_commands.Group(name="infantry", description="Infantry roster management")

    # /infantry view <soldier>
    @infantry.command(name="view", description="View a soldier's full stats card")
    @app_commands.describe(soldier="Start typing a soldier name")
    @requires_django_perm(_VIEW_PERM)
    async def infantry_view(self, interaction: discord.Interaction, soldier: str):
        async def _show(inter: discord.Interaction, soldier_name: str):
            from app.infantryboard.models import Soldier
            obj = await Soldier.objects.filter(name__iexact=soldier_name).prefetch_related("goals").afirst()
            if not obj:
                await inter.edit_original_response(content=f"No soldier named **{soldier_name}** found.", embed=None)
                return
            open_goal = await obj.goals.filter(completed=False).afirst()
            await inter.edit_original_response(content=None, embed=_soldier_embed(obj, open_goal))

        await interaction.response.send_message(
            _PII_WARNING, view=_PIIConfirmView(_show, soldier), ephemeral=True
        )

    @infantry_view.autocomplete("soldier")
    async def infantry_view_ac(self, interaction, current):
        return await _soldier_autocomplete(interaction, current)

    # /infantry add <member> [name]
    @infantry.command(name="add", description="Add a Discord member to the infantry roster")
    @app_commands.describe(
        member="The Discord member to register",
        name="Override display name (defaults to member's nickname / username)",
    )
    @requires_django_perm(_MANAGE_PERM)
    async def infantry_add(self, interaction: discord.Interaction, member: discord.Member, name: str = ""):
        from app.infantryboard.models import Soldier
        from app.unifieduser.models import OrgPlayer

        await interaction.response.defer(ephemeral=True)

        soldier_name = (
            name.strip()
            or member.display_name
            or member.global_name
            or member.name
        )

        if await Soldier.objects.filter(name__iexact=soldier_name).aexists():
            await interaction.followup.send(
                f"A soldier named **{soldier_name}** already exists.", ephemeral=True
            )
            return

        author = await OrgPlayer.objects.filter(discorduser__discorduid=interaction.user.id).afirst()
        linked_player = await OrgPlayer.objects.filter(discorduser__discorduid=member.id).afirst()
        obj = await Soldier.objects.acreate(
            name=soldier_name,
            updated_by=author,
            org_player=linked_player,
        )
        await interaction.followup.send(
            f"✅ Added **{obj.name}** ({member.mention}) to the infantry roster.", ephemeral=True
        )

    # /infantry delete <soldier>
    @infantry.command(name="delete", description="Remove a soldier from the roster")
    @app_commands.describe(soldier="Start typing a soldier name")
    @requires_django_perm(_MANAGE_PERM)
    async def infantry_delete(self, interaction: discord.Interaction, soldier: str):
        from app.infantryboard.models import Soldier

        await interaction.response.defer(ephemeral=True)
        obj = await Soldier.objects.filter(name__iexact=soldier).afirst()
        if not obj:
            await interaction.followup.send(f"No soldier named **{soldier}** found.", ephemeral=True)
            return
        await obj.adelete()
        await interaction.followup.send(f"🗑️ Removed **{soldier}** from the roster.", ephemeral=True)

    @infantry_delete.autocomplete("soldier")
    async def infantry_delete_ac(self, interaction, current):
        return await _soldier_autocomplete(interaction, current)

    # /infantry rate <soldier> <skill> <value>
    @infantry.command(name="rate", description="Update a soldier's skill rating")
    @app_commands.describe(
        soldier="Start typing a soldier name",
        skill="Skill to rate",
        value="Rating 0–5",
    )
    @app_commands.choices(skill=_SKILL_CHOICES)
    @requires_django_perm(_MANAGE_PERM)
    async def infantry_rate(
        self,
        interaction: discord.Interaction,
        soldier: str,
        skill: app_commands.Choice[str],
        value: int,
    ):
        from app.infantryboard.models import Soldier
        from app.unifieduser.models import OrgPlayer

        if not 0 <= value <= 5:
            await interaction.response.send_message("Rating must be between 0 and 5.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        obj = await Soldier.objects.filter(name__iexact=soldier).afirst()
        if not obj:
            await interaction.followup.send(f"No soldier named **{soldier}** found.", ephemeral=True)
            return

        author = await OrgPlayer.objects.filter(discorduser__discorduid=interaction.user.id).afirst()
        setattr(obj, skill.value, value)
        obj.updated_by = author
        await obj.asave(update_fields=[skill.value, "updated_by"])
        await interaction.followup.send(
            f"✅ **{obj.name}** — {skill.name}: {_skull_bar(value)}", ephemeral=True
        )

    @infantry_rate.autocomplete("soldier")
    async def infantry_rate_ac(self, interaction, current):
        return await _soldier_autocomplete(interaction, current)

    # /infantry tag <soldier> <tag> <action>
    @infantry.command(name="tag", description="Add or remove a tag from a soldier")
    @app_commands.describe(
        soldier="Start typing a soldier name",
        tag="Tag to change",
        action="add or remove",
    )
    @app_commands.choices(
        tag=_TAG_CHOICES,
        action=[
            app_commands.Choice(name="add",    value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ],
    )
    @requires_django_perm(_MANAGE_PERM)
    async def infantry_tag(
        self,
        interaction: discord.Interaction,
        soldier: str,
        tag: app_commands.Choice[str],
        action: app_commands.Choice[str],
    ):
        from app.infantryboard.models import Soldier
        from app.unifieduser.models import OrgPlayer

        await interaction.response.defer(ephemeral=True)
        obj = await Soldier.objects.filter(name__iexact=soldier).afirst()
        if not obj:
            await interaction.followup.send(f"No soldier named **{soldier}** found.", ephemeral=True)
            return

        current_tags = list(obj.tags or [])
        if action.value == "add":
            if tag.value in current_tags:
                await interaction.followup.send(
                    f"**{obj.name}** already has the **{tag.name}** tag.", ephemeral=True
                )
                return
            current_tags.append(tag.value)
        else:
            if tag.value not in current_tags:
                await interaction.followup.send(
                    f"**{obj.name}** doesn't have the **{tag.name}** tag.", ephemeral=True
                )
                return
            current_tags.remove(tag.value)

        author = await OrgPlayer.objects.filter(discorduser__discorduid=interaction.user.id).afirst()
        obj.tags = current_tags
        obj.updated_by = author
        await obj.asave(update_fields=["tags", "updated_by"])
        verb = "Added" if action.value == "add" else "Removed"
        await interaction.followup.send(
            f"✅ {verb} **{tag.name}** tag for **{obj.name}**.", ephemeral=True
        )

    @infantry_tag.autocomplete("soldier")
    async def infantry_tag_ac(self, interaction, current):
        return await _soldier_autocomplete(interaction, current)

    # /infantry status <soldier> <active|inactive>
    @infantry.command(name="status", description="Set a soldier's active or inactive status")
    @app_commands.describe(soldier="Start typing a soldier name", status="active or inactive")
    @app_commands.choices(status=[
        app_commands.Choice(name="Active",   value="active"),
        app_commands.Choice(name="Inactive", value="inactive"),
    ])
    @requires_django_perm(_MANAGE_PERM)
    async def infantry_status(
        self,
        interaction: discord.Interaction,
        soldier: str,
        status: app_commands.Choice[str],
    ):
        from app.infantryboard.models import Soldier
        from app.unifieduser.models import OrgPlayer

        await interaction.response.defer(ephemeral=True)
        obj = await Soldier.objects.filter(name__iexact=soldier).afirst()
        if not obj:
            await interaction.followup.send(f"No soldier named **{soldier}** found.", ephemeral=True)
            return

        is_active = status.value == "active"
        if obj.is_active == is_active:
            await interaction.followup.send(
                f"**{obj.name}** is already **{status.name.lower()}**.", ephemeral=True
            )
            return

        author = await OrgPlayer.objects.filter(discorduser__discorduid=interaction.user.id).afirst()
        obj.is_active  = is_active
        obj.updated_by = author
        await obj.asave(update_fields=["is_active", "updated_by"])
        icon = "✅" if is_active else "💤"
        await interaction.followup.send(
            f"{icon} **{obj.name}** is now **{status.name.lower()}**.", ephemeral=True
        )

    @infantry_status.autocomplete("soldier")
    async def infantry_status_ac(self, interaction, current):
        return await _soldier_autocomplete(interaction, current)

    # -----------------------------------------------------------------------
    # /infantry goal sub-group
    # -----------------------------------------------------------------------

    goal = app_commands.Group(name="goal", description="Infantry goal management", parent=infantry)

    # /infantry goal set <soldier> <text>
    @goal.command(name="set", description="Set a new goal for a soldier (must have no open goal)")
    @app_commands.describe(soldier="Start typing a soldier name", text="Goal description")
    @requires_django_perm(_MANAGE_PERM)
    async def goal_set(self, interaction: discord.Interaction, soldier: str, text: str):
        from app.infantryboard.models import Soldier, SoldierGoal

        await interaction.response.defer(ephemeral=True)
        obj = await Soldier.objects.filter(name__iexact=soldier).afirst()
        if not obj:
            await interaction.followup.send(f"No soldier named **{soldier}** found.", ephemeral=True)
            return

        open_goal = await SoldierGoal.objects.filter(soldier=obj, completed=False).afirst()
        if open_goal:
            await interaction.followup.send(
                f"**{obj.name}** already has an open goal: *{open_goal.title}*\n"
                "Complete it first with `/infantry goal complete`.",
                ephemeral=True,
            )
            return

        await SoldierGoal.objects.acreate(soldier=obj, title=text)
        await interaction.followup.send(
            f"✅ Goal set for **{obj.name}**: *{text}*", ephemeral=True
        )

    @goal_set.autocomplete("soldier")
    async def goal_set_ac(self, interaction, current):
        return await _soldier_autocomplete(interaction, current)

    # /infantry goal complete <soldier>
    @goal.command(name="complete", description="Mark a soldier's current goal as completed")
    @app_commands.describe(soldier="Start typing a soldier name")
    @requires_django_perm(_MANAGE_PERM)
    async def goal_complete(self, interaction: discord.Interaction, soldier: str):
        from app.infantryboard.models import Soldier, SoldierGoal
        from app.preferences.utils import aget_global_preference
        from app.infantryboard import preferences as prefs

        await interaction.response.defer(ephemeral=True)
        obj = await Soldier.objects.filter(name__iexact=soldier).afirst()
        if not obj:
            await interaction.followup.send(f"No soldier named **{soldier}** found.", ephemeral=True)
            return

        open_goal = await SoldierGoal.objects.filter(soldier=obj, completed=False).afirst()
        if not open_goal:
            await interaction.followup.send(f"**{obj.name}** has no open goal.", ephemeral=True)
            return

        open_goal.completed = True
        await open_goal.asave(update_fields=["completed"])
        await interaction.followup.send(
            f"✅ Goal completed for **{obj.name}**: *{open_goal.title}*", ephemeral=True
        )

        # Optional channel announcement
        try:
            announce = await aget_global_preference(prefs.InfantryRosterPublicUpdates.import_path)
            if announce:
                channel_id = await aget_global_preference(prefs.InfantryRosterChannelID.import_path)
                if channel_id:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel:
                        embed = discord.Embed(
                            description=f"✅ **{obj.name}** completed a goal: *{open_goal.title}*",
                            color=0x22C55E,
                        )
                        await channel.send(embed=embed)
        except Exception:
            log.exception("infantryboard: failed to send goal-complete announcement")

    @goal_complete.autocomplete("soldier")
    async def goal_complete_ac(self, interaction, current):
        return await _soldier_autocomplete(interaction, current)

    # -----------------------------------------------------------------------
    # Error handler
    # -----------------------------------------------------------------------

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingDjangoPermission):
            msg = f"🛡️ **Permission denied:** you need `{error.missing_perm}`."
        elif isinstance(error, AccountNotLinked):
            msg = "🎮 Your Discord account isn't linked to an org profile yet."
        else:
            msg = "❌ An unexpected error occurred."
            log.exception("InfantryboardCog command error: %s", error)

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(InfantryboardCog(bot))
