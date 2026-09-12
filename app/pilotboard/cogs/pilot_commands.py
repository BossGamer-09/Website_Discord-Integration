"""
app/pilotboard/cogs/pilot_commands.py

Discord slash commands for the Pilot Roster system.

Commands (all under /pilot group):
  /pilot list                           — paginated roster embed (view perm)
  /pilot view <pilot>                   — full stats card (autocomplete, view perm)
  /pilot add <member> [name]            — create pilot from a Discord member (manage perm)
  /pilot delete <pilot>                 — remove a pilot (autocomplete, manage perm)
  /pilot rate <pilot> <skill> <value>   — update skill score (autocomplete, manage perm)
  /pilot tag <pilot> <tag> <action>     — add/remove tag (autocomplete, manage perm)
  /pilot status <pilot> <active|inactive> — toggle pilot active status (manage perm)
  /pilot goal set <pilot> <text>        — set new goal (autocomplete, manage perm)
  /pilot goal complete <pilot>          — mark current goal done (autocomplete, manage perm)

Pilot selection uses Discord autocomplete for existing entries.
The /pilot add command accepts a discord.Member so users are chosen
from the server member list rather than typed by hand.
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
from app.pilotboard.models import PilotTag

log = logging.getLogger(__name__)

_PII_WARNING = (
    "⚠️ **Personal Information Notice**\n\n"
    "The information you are about to view may include personal details about "
    "org members (display names, skill assessments, goals, and linked accounts).\n\n"
    "**This information is confidential.** Do not share, screenshot, or distribute "
    "it outside of authorised staff channels.\n\n"
    "By pressing **Confirm** you acknowledge this policy."
)

_VIEW_PERM   = "pilotboard.view_pilot_roster"
_MANAGE_PERM = "pilotboard.manage_pilot_roster"

_TAG_LABELS = {t.value: t.label for t in PilotTag}
_SKILL_CHOICES = [
    app_commands.Choice(name="Team Fighting", value="team_fighting"),
    app_commands.Choice(name="Duelling",      value="duelling"),
    app_commands.Choice(name="Leadership",    value="leadership"),
]
_TAG_CHOICES = [app_commands.Choice(name=t.label, value=t.value) for t in PilotTag]

_TIER = {0: "—", 1: "Below Standard", 2: "Legion Standard", 3: "Knight Standard", 4: "Above Standard", 5: "Top 50"}
_SKULL_FILLED = "💀"
_SKULL_EMPTY  = "⬜"


def _skull_bar(n: int) -> str:
    return _SKULL_FILLED * n + _SKULL_EMPTY * (5 - n)


def _pilot_embed(pilot, open_goal=None) -> discord.Embed:
    embed = discord.Embed(title=pilot.name, color=0x7C3AED)
    if not pilot.is_active:
        embed.description = "🔴 **Inactive**"
    embed.add_field(
        name="Team Fighting",
        value=f"{_skull_bar(pilot.team_fighting)}\n{_TIER[pilot.team_fighting]}",
        inline=True,
    )
    embed.add_field(
        name="Duelling",
        value=f"{_skull_bar(pilot.duelling)}\n{_TIER[pilot.duelling]}",
        inline=True,
    )
    embed.add_field(
        name="Leadership",
        value=f"{_skull_bar(pilot.leadership)}\n{_TIER[pilot.leadership]}",
        inline=True,
    )
    if open_goal:
        embed.add_field(name="Current Goal", value=f"*{open_goal.title}*", inline=False)
    if pilot.updated_by:
        embed.set_footer(text=f"Last updated by {pilot.updated_by.display_name}")
    return embed


# ---------------------------------------------------------------------------
# Shared autocomplete helper
# ---------------------------------------------------------------------------

async def _pilot_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Return up to 25 pilot names matching the current input."""
    from app.pilotboard.models import Pilot
    choices = []
    qs = Pilot.objects.order_by("name")
    if current:
        qs = qs.filter(name__icontains=current)
    async for p in qs[:25]:
        choices.append(app_commands.Choice(name=p.name, value=p.name))
    return choices


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class _PIIConfirmView(discord.ui.View):
    """Ephemeral one-shot PII acknowledgement gate."""

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
        pass  # ephemeral messages clean themselves up


class PilotboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------------------------------------------------
    # /pilot group
    # -----------------------------------------------------------------------

    pilot = app_commands.Group(name="pilot", description="Pilot roster management")

    # /pilot view <pilot>
    @pilot.command(name="view", description="View a pilot's full stats card")
    @app_commands.describe(pilot="Start typing a pilot name")
    @requires_django_perm(_VIEW_PERM)
    async def pilot_view(self, interaction: discord.Interaction, pilot: str):
        async def _show(inter: discord.Interaction, pilot_name: str):
            from app.pilotboard.models import Pilot
            obj = await Pilot.objects.filter(name__iexact=pilot_name).prefetch_related("goals").afirst()
            if not obj:
                await inter.edit_original_response(content=f"No pilot named **{pilot_name}** found.", embed=None)
                return
            open_goal = await obj.goals.filter(completed=False).afirst()
            await inter.edit_original_response(content=None, embed=_pilot_embed(obj, open_goal))

        await interaction.response.send_message(
            _PII_WARNING, view=_PIIConfirmView(_show, pilot), ephemeral=True
        )

    @pilot_view.autocomplete("pilot")
    async def pilot_view_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _pilot_autocomplete(interaction, current)

    # /pilot add <member> [name]
    @pilot.command(name="add", description="Add a Discord member to the pilot roster")
    @app_commands.describe(
        member="The Discord member to register as a pilot",
        name="Override display name (defaults to member's server nickname / username)",
    )
    @requires_django_perm(_MANAGE_PERM)
    async def pilot_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        name: str = "",
    ):
        from app.pilotboard.models import Pilot
        from app.unifieduser.models import OrgPlayer

        await interaction.response.defer(ephemeral=True)

        # Resolve the pilot name: explicit override → server nick → global name → username
        pilot_name = (
            name.strip()
            or member.display_name
            or member.global_name
            or member.name
        )

        if await Pilot.objects.filter(name__iexact=pilot_name).aexists():
            await interaction.followup.send(
                f"A pilot named **{pilot_name}** already exists.", ephemeral=True
            )
            return

        author = await OrgPlayer.objects.filter(
            discorduser__discorduid=interaction.user.id
        ).afirst()
        # Link the pilot to the Discord member's OrgPlayer account (if registered)
        linked_player = await OrgPlayer.objects.filter(
            discorduser__discorduid=member.id
        ).afirst()
        obj = await Pilot.objects.acreate(
            name=pilot_name,
            updated_by=author,
            org_player=linked_player,
        )
        await interaction.followup.send(
            f"✅ Added **{obj.name}** ({member.mention}) to the roster.", ephemeral=True
        )

    # /pilot delete <pilot>
    @pilot.command(name="delete", description="Remove a pilot from the roster")
    @app_commands.describe(pilot="Start typing a pilot name")
    @requires_django_perm(_MANAGE_PERM)
    async def pilot_delete(self, interaction: discord.Interaction, pilot: str):
        from app.pilotboard.models import Pilot

        await interaction.response.defer(ephemeral=True)
        obj = await Pilot.objects.filter(name__iexact=pilot).afirst()
        if not obj:
            await interaction.followup.send(f"No pilot named **{pilot}** found.", ephemeral=True)
            return
        await obj.adelete()
        await interaction.followup.send(f"🗑️ Removed **{pilot}** from the roster.", ephemeral=True)

    @pilot_delete.autocomplete("pilot")
    async def pilot_delete_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _pilot_autocomplete(interaction, current)

    # /pilot rate <pilot> <skill> <value>
    @pilot.command(name="rate", description="Update a pilot's skill rating")
    @app_commands.describe(
        pilot="Start typing a pilot name",
        skill="Skill to rate",
        value="Rating 0–5",
    )
    @app_commands.choices(skill=_SKILL_CHOICES)
    @requires_django_perm(_MANAGE_PERM)
    async def pilot_rate(
        self,
        interaction: discord.Interaction,
        pilot: str,
        skill: app_commands.Choice[str],
        value: int,
    ):
        from app.pilotboard.models import Pilot
        from app.unifieduser.models import OrgPlayer

        if not 0 <= value <= 5:
            await interaction.response.send_message("Rating must be between 0 and 5.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        obj = await Pilot.objects.filter(name__iexact=pilot).afirst()
        if not obj:
            await interaction.followup.send(f"No pilot named **{pilot}** found.", ephemeral=True)
            return

        author = await OrgPlayer.objects.filter(
            discorduser__discorduid=interaction.user.id
        ).afirst()
        setattr(obj, skill.value, value)
        obj.updated_by = author
        await obj.asave(update_fields=[skill.value, "updated_by"])
        await interaction.followup.send(
            f"✅ **{obj.name}** — {skill.name}: {_skull_bar(value)}", ephemeral=True
        )

    @pilot_rate.autocomplete("pilot")
    async def pilot_rate_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _pilot_autocomplete(interaction, current)

    # /pilot tag <pilot> <tag> <action>
    @pilot.command(name="tag", description="Add or remove a tag from a pilot")
    @app_commands.describe(
        pilot="Start typing a pilot name",
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
    async def pilot_tag(
        self,
        interaction: discord.Interaction,
        pilot: str,
        tag: app_commands.Choice[str],
        action: app_commands.Choice[str],
    ):
        from app.pilotboard.models import Pilot
        from app.unifieduser.models import OrgPlayer

        await interaction.response.defer(ephemeral=True)
        obj = await Pilot.objects.filter(name__iexact=pilot).afirst()
        if not obj:
            await interaction.followup.send(f"No pilot named **{pilot}** found.", ephemeral=True)
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

        author = await OrgPlayer.objects.filter(
            discorduser__discorduid=interaction.user.id
        ).afirst()
        obj.tags = current_tags
        obj.updated_by = author
        await obj.asave(update_fields=["tags", "updated_by"])
        verb = "Added" if action.value == "add" else "Removed"
        await interaction.followup.send(
            f"✅ {verb} **{tag.name}** tag for **{obj.name}**.", ephemeral=True
        )

    @pilot_tag.autocomplete("pilot")
    async def pilot_tag_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _pilot_autocomplete(interaction, current)

    # -----------------------------------------------------------------------
    # /pilot goal sub-group
    # -----------------------------------------------------------------------

    goal = app_commands.Group(name="goal", description="Pilot goal management", parent=pilot)

    # /pilot goal set <pilot> <text>
    @goal.command(name="set", description="Set a new goal for a pilot (must have no open goal)")
    @app_commands.describe(pilot="Start typing a pilot name", text="Goal description")
    @requires_django_perm(_MANAGE_PERM)
    async def goal_set(self, interaction: discord.Interaction, pilot: str, text: str):
        from app.pilotboard.models import Pilot, PilotGoal

        await interaction.response.defer(ephemeral=True)
        obj = await Pilot.objects.filter(name__iexact=pilot).afirst()
        if not obj:
            await interaction.followup.send(f"No pilot named **{pilot}** found.", ephemeral=True)
            return

        open_goal = await PilotGoal.objects.filter(pilot=obj, completed=False).afirst()
        if open_goal:
            await interaction.followup.send(
                f"**{obj.name}** already has an open goal: *{open_goal.title}*\n"
                "Complete it first with `/pilot goal complete`.",
                ephemeral=True,
            )
            return

        await PilotGoal.objects.acreate(pilot=obj, title=text)
        await interaction.followup.send(
            f"✅ Goal set for **{obj.name}**: *{text}*", ephemeral=True
        )

    @goal_set.autocomplete("pilot")
    async def goal_set_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _pilot_autocomplete(interaction, current)

    # /pilot goal complete <pilot>
    @goal.command(name="complete", description="Mark a pilot's current goal as completed")
    @app_commands.describe(pilot="Start typing a pilot name")
    @requires_django_perm(_MANAGE_PERM)
    async def goal_complete(self, interaction: discord.Interaction, pilot: str):
        from app.pilotboard.models import Pilot, PilotGoal
        from app.preferences.utils import aget_global_preference
        from app.pilotboard import preferences as prefs

        await interaction.response.defer(ephemeral=True)
        obj = await Pilot.objects.filter(name__iexact=pilot).afirst()
        if not obj:
            await interaction.followup.send(f"No pilot named **{pilot}** found.", ephemeral=True)
            return

        open_goal = await PilotGoal.objects.filter(pilot=obj, completed=False).afirst()
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
            announce = await aget_global_preference(prefs.PilotRosterPublicUpdates.import_path)
            if announce:
                channel_id = await aget_global_preference(prefs.PilotRosterChannelID.import_path)
                if channel_id:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel:
                        embed = discord.Embed(
                            description=f"✅ **{obj.name}** completed a goal: *{open_goal.title}*",
                            color=0x22C55E,
                        )
                        await channel.send(embed=embed)
        except Exception:
            log.exception("pilotboard: failed to send goal-complete announcement")

    @goal_complete.autocomplete("pilot")
    async def goal_complete_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _pilot_autocomplete(interaction, current)

    # /pilot status <pilot> <active|inactive>
    @pilot.command(name="status", description="Set a pilot's active or inactive status")
    @app_commands.describe(pilot="Start typing a pilot name", status="active or inactive")
    @app_commands.choices(status=[
        app_commands.Choice(name="Active",   value="active"),
        app_commands.Choice(name="Inactive", value="inactive"),
    ])
    @requires_django_perm(_MANAGE_PERM)
    async def pilot_status(
        self,
        interaction: discord.Interaction,
        pilot: str,
        status: app_commands.Choice[str],
    ):
        from app.pilotboard.models import Pilot
        from app.unifieduser.models import OrgPlayer

        await interaction.response.defer(ephemeral=True)
        obj = await Pilot.objects.filter(name__iexact=pilot).afirst()
        if not obj:
            await interaction.followup.send(f"No pilot named **{pilot}** found.", ephemeral=True)
            return

        is_active = status.value == "active"
        if obj.is_active == is_active:
            await interaction.followup.send(
                f"**{obj.name}** is already **{status.name.lower()}**.", ephemeral=True
            )
            return

        author = await OrgPlayer.objects.filter(
            discorduser__discorduid=interaction.user.id
        ).afirst()
        obj.is_active  = is_active
        obj.updated_by = author
        await obj.asave(update_fields=["is_active", "updated_by"])
        icon = "✅" if is_active else "💤"
        await interaction.followup.send(
            f"{icon} **{obj.name}** is now **{status.name.lower()}**.", ephemeral=True
        )

    @pilot_status.autocomplete("pilot")
    async def pilot_status_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _pilot_autocomplete(interaction, current)

    # -----------------------------------------------------------------------
    # Error handler
    # -----------------------------------------------------------------------

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, MissingDjangoPermission):
            msg = f"🛡️ **Permission denied:** you need `{error.missing_perm}`."
        elif isinstance(error, AccountNotLinked):
            msg = "🎮 Your Discord account isn't linked to an org profile yet."
        else:
            msg = "❌ An unexpected error occurred."
            log.exception("PilotboardCog command error: %s", error)

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PilotboardCog(bot))
