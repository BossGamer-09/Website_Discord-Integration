"""
app/org/cogs/activity_ping_cog.py

Self-assignable activity ping roles portal, grouped by category.

/activity_ping setup   — [Staff] post/refresh the portal embed in the configured channel
/activity_ping refresh — [Staff] re-render the portal embed in place
/activity_ping add     — [Staff] add a Discord role to the ping portal
/activity_ping remove  — [Staff] remove a role from the ping portal

/self_assign_roles — ephemeral select-menu for members to add/remove their own roles

The portal embed groups buttons by category (e.g. "Discipline", "Other Roles").
Members click a button to add/remove the role from themselves.
Rank/hierarchy roles are blocked from being added.
"""
import logging
from itertools import groupby

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Select
from asgiref.sync import sync_to_async
from django.apps import apps

from app.main.util.discord_command_checks import requires_django_perm
from app.preferences.utils import aget_global_preference

logger = logging.getLogger(__name__)

CATEGORY_DISCIPLINE = "Discipline"
CATEGORY_OTHER      = "Other Roles"


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

async def _check_rank_denied(member: discord.Member, min_rank) -> str | None:
    """Return an error string if the member's OrgRank is below min_rank, else None."""
    DiscordUser = apps.get_model('discordauth', 'DiscordUser')

    def _get_rank_order():
        try:
            du = DiscordUser.objects.select_related('user__rank').get(discorduid=member.id)
            player = du.user
            if player is None or player.rank is None:
                return None
            return player.rank.order
        except DiscordUser.DoesNotExist:
            return None

    member_order = await sync_to_async(_get_rank_order)()
    # OrgRank extends OrderedModel; lower `order` = higher rank (position 0 is top)
    if member_order is None or member_order > min_rank.order:
        return (
            f"❌ You need to be at least **{min_rank.name}** to join this interest.\n"
            "Contact staff if you believe this is an error."
        )
    return None


async def _log_role_note(member: discord.Member, message: str) -> None:
    OrgPlayerNote = apps.get_model('org', 'OrgPlayerNote')
    DiscordUser   = apps.get_model('discordauth', 'DiscordUser')

    def _write():
        try:
            du = DiscordUser.objects.select_related('user').get(discorduid=member.id)
            if du.user:
                OrgPlayerNote.objects.create(user=du.user, message=message)
        except DiscordUser.DoesNotExist:
            pass

    await sync_to_async(_write)()


def _make_toggle_callback(discord_role_id: int, label: str, min_rank=None):
    async def callback(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        role = interaction.guild.get_role(discord_role_id)
        if not role:
            await interaction.followup.send("❌ Role not found — please contact staff.", ephemeral=True)
            return
        member = interaction.user
        if role not in member.roles and min_rank is not None:
            denied = await _check_rank_denied(member, min_rank)
            if denied:
                await interaction.followup.send(denied, ephemeral=True)
                return
        if role in member.roles:
            await member.remove_roles(role, reason="Activity ping self-remove")
            await _log_role_note(member, f"Self-removed interest role: {label}")
            await interaction.followup.send(f"✅ Removed **{label}** pings from you.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Activity ping self-add")
            await _log_role_note(member, f"Self-assigned interest role: {label}")
            await interaction.followup.send(f"✅ You'll now receive **{label}** pings!", ephemeral=True)
    return callback


async def _fetch_active_roles() -> list:
    ActivityPingRole = apps.get_model('org', 'ActivityPingRole')
    return await sync_to_async(list)(
        ActivityPingRole.objects.filter(is_active=True)
        .select_related('discord_role', 'min_rank')
        .order_by('category', 'display_order', 'label')
    )


def _build_embed_and_views(roles: list) -> tuple[discord.Embed, list[View]]:
    """
    Returns one embed describing all categories and one View per category
    (Discord limits 25 components / view and 5 action rows, so splitting by
    category keeps us well within limits).
    """
    embed = discord.Embed(
        title="🔔 Gameplay Interests & Role Selection",
        description=(
            "Our organization offers various specialized paths. Joining an Interest "
            "indicates this with a role and will get you pinged for this gameplay more, "
            "and is your **1st step** to joining unique training programs and specialized operations.\n\n"
            "**Eligibility & Selection**\nClick a button below to **add** or **remove** yourself from a role."
        ),
        color=0x5865F2,
    )

    views: list[View] = []

    from itertools import groupby as _groupby
    for cat, group_iter in _groupby(roles, key=lambda r: r.category or CATEGORY_OTHER):
        group = list(group_iter)
        embed.add_field(
            name=f"━━ {cat} ━━",
            value="\n".join(
                f"{r.emoji + ' ' if r.emoji else ''}"
                f"**{r.label}**"
                f"{(' — ' + r.description) if r.description else ''}"
                for r in group
            ) or "—",
            inline=False,
        )

        v = View(timeout=None)
        for r in group:
            btn_label = (r.emoji + " " if r.emoji else "") + r.label
            btn = Button(
                label=btn_label[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"actping_toggle_{r.discord_role_id}",
            )
            btn.callback = _make_toggle_callback(r.discord_role_id, r.label, r.min_rank)
            v.add_item(btn)
        views.append(v)

    if not roles:
        embed.description = (embed.description or "") + "\n\n*No roles configured yet.*"

    return embed, views


async def _post_or_update_portal(guild: discord.Guild, bot) -> str:
    from app.org.preferences import ActivityPingPortalChannelID
    ActivityPingPortal = apps.get_model('org', 'ActivityPingPortal')

    chan_id = await aget_global_preference(ActivityPingPortalChannelID.import_path)
    if not chan_id:
        return "❌ `ActivityPingPortalChannelID` preference is not set."

    chan = guild.get_channel(int(chan_id))
    if not chan:
        return "❌ Portal channel not found — check the `ActivityPingPortalChannelID` preference."

    roles = await _fetch_active_roles()
    embed, views = _build_embed_and_views(roles)

    def _get_portal():
        return ActivityPingPortal.objects.filter(channel_id=int(chan_id)).first()

    portal = await sync_to_async(_get_portal)()

    if portal and portal.message_id:
        try:
            old_msg = await chan.fetch_message(portal.message_id)
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    first_view = views[0] if views else View(timeout=None)
    msg = await chan.send(embed=embed, view=first_view)
    for extra_view in views[1:]:
        await chan.send(view=extra_view)

    for v in views:
        bot.add_view(v)

    def _save_portal():
        ActivityPingPortal.objects.update_or_create(
            channel_id=int(chan_id),
            defaults={'message_id': msg.id},
        )
    await sync_to_async(_save_portal)()
    return "✅ Portal posted."


async def _is_rank_role(role: discord.Role) -> bool:
    """True if this Discord role is tied to an OrgRank (via GroupDiscordRole → rank groups)."""
    GroupDiscordRole = apps.get_model('org', 'GroupDiscordRole')
    OrgRank = apps.get_model('unifieduser', 'OrgRank')
    return await sync_to_async(
        lambda: GroupDiscordRole.objects.filter(
            discord_role_id=role.id,
            group__ranks__isnull=False,
        ).exists()
    )()


# ------------------------------------------------------------------ #
# Self-assign select-menu view                                         #
# ------------------------------------------------------------------ #

class SelfAssignRolesView(View):
    """
    Ephemeral select-menu UI — one Select per category (max 25 options each).
    Roles the member already holds are pre-selected.
    Each select fires independently; changes apply immediately.
    """

    def __init__(self, member: discord.Member, roles: list):
        super().__init__(timeout=120)
        member_role_ids = {r.id for r in member.roles}

        for cat, group_iter in groupby(roles, key=lambda r: r.category or CATEGORY_OTHER):
            group = list(group_iter)[:25]
            options = [
                discord.SelectOption(
                    label=(f"{r.emoji} " if r.emoji else "") + r.label[:80],
                    value=str(r.discord_role_id),
                    description=r.description[:100] if r.description else None,
                    default=r.discord_role_id in member_role_ids,
                )
                for r in group
            ]
            sel = Select(
                placeholder=f"{cat} — select roles to add/remove",
                options=options,
                min_values=0,
                max_values=len(options),
            )
            sel.callback = self._make_select_callback(group, member)
            self.add_item(sel)

    @staticmethod
    def _make_select_callback(group: list, member: discord.Member):
        group_role_ids = {r.discord_role_id for r in group}
        min_rank_map   = {r.discord_role_id: r.min_rank for r in group}
        label_map      = {r.discord_role_id: r.label for r in group}

        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            chosen_ids  = {int(v) for v in interaction.data.get("values", [])}
            current_ids = {r.id for r in member.roles} & group_role_ids

            to_add    = chosen_ids - current_ids
            to_remove = current_ids - chosen_ids

            added, removed, denied = [], [], []
            for role_id in to_add:
                min_rank = min_rank_map.get(role_id)
                if min_rank is not None:
                    err = await _check_rank_denied(member, min_rank)
                    if err:
                        denied.append(label_map.get(role_id, str(role_id)))
                        continue
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await member.add_roles(role, reason="Self-assign roles")
                        await _log_role_note(member, f"Self-assigned interest role: {label_map.get(role_id, role.name)}")
                        added.append(role.name)
                    except discord.Forbidden:
                        logger.warning("Missing perms to add role %s to %s", role_id, member)
            for role_id in to_remove:
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await member.remove_roles(role, reason="Self-assign roles")
                        await _log_role_note(member, f"Self-removed interest role: {label_map.get(role_id, role.name)}")
                        removed.append(role.name)
                    except discord.Forbidden:
                        logger.warning("Missing perms to remove role %s from %s", role_id, member)

            lines = []
            if added:
                lines.append("✅ **Added:** " + ", ".join(f"**{n}**" for n in added))
            if removed:
                lines.append("➖ **Removed:** " + ", ".join(f"**{n}**" for n in removed))
            if denied:
                lines.append("🔒 **Rank required:** " + ", ".join(f"**{n}**" for n in denied))
            if not lines:
                lines.append("No changes made.")

            await interaction.followup.send("\n".join(lines), ephemeral=True)

        return callback


# ------------------------------------------------------------------ #
# Cog                                                                  #
# ------------------------------------------------------------------ #

class ActivityPingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        roles = await _fetch_active_roles()
        if roles:
            _, views = _build_embed_and_views(roles)
            for v in views:
                self.bot.add_view(v)

    ping = app_commands.Group(name="activity_ping", description="Activity ping role portal management")

    @ping.command(name="setup", description="[Staff] Post or refresh the activity ping portal")
    @requires_django_perm("org.manage_activity_ping_roles")
    async def ping_setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await _post_or_update_portal(interaction.guild, self.bot)
        await interaction.followup.send(result, ephemeral=True)

    @ping.command(name="refresh", description="[Staff] Re-render the portal embed in place")
    @requires_django_perm("org.manage_activity_ping_roles")
    async def ping_refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await _post_or_update_portal(interaction.guild, self.bot)
        await interaction.followup.send(result, ephemeral=True)

    @ping.command(name="add", description="[Staff] Add a Discord role to the activity ping portal")
    @app_commands.describe(
        role="Discord role to add",
        label="Display name in the portal",
        category='Section (e.g. "Discipline" or "Other Roles")',
        description="Short description shown under the role",
        emoji="Emoji shown next to the label (optional)",
        display_order="Sort order within its category (lower = first)",
    )
    @requires_django_perm("org.manage_activity_ping_roles")
    async def ping_add(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        label: str,
        category: str = CATEGORY_DISCIPLINE,
        description: str = "",
        emoji: str = "",
        display_order: int = 0,
    ):
        await interaction.response.defer(ephemeral=True)

        if await _is_rank_role(role):
            await interaction.followup.send(
                f"❌ **{role.name}** is linked to an OrgRank and cannot be added as a self-assignable ping role.",
                ephemeral=True,
            )
            return

        ActivityPingRole = apps.get_model('org', 'ActivityPingRole')
        DiscordRole      = apps.get_model('org', 'DiscordRole')

        def _create():
            dr, _ = DiscordRole.objects.get_or_create(role_id=role.id)
            obj, created = ActivityPingRole.objects.get_or_create(
                discord_role=dr,
                defaults={
                    'label': label,
                    'description': description,
                    'emoji': emoji,
                    'category': category,
                    'display_order': display_order,
                    'is_active': True,
                },
            )
            if not created:
                obj.label         = label
                obj.description   = description
                obj.emoji         = emoji
                obj.category      = category
                obj.display_order = display_order
                obj.is_active     = True
                obj.save()
            return created

        created = await sync_to_async(_create)()
        word = "Added" if created else "Updated"
        await interaction.followup.send(
            f"✅ {word} **{label}** (`{role.name}`) in the **{category}** section.\n"
            "Run `/activity_ping refresh` to update the embed.",
            ephemeral=True,
        )

    @ping_add.autocomplete("category")
    async def category_autocomplete(self, interaction: discord.Interaction, current: str):
        ActivityPingRole = apps.get_model('org', 'ActivityPingRole')
        existing = await sync_to_async(list)(
            ActivityPingRole.objects.values_list('category', flat=True).distinct()
        )
        defaults = [CATEGORY_DISCIPLINE, CATEGORY_OTHER]
        options = list(dict.fromkeys(defaults + existing))
        return [
            app_commands.Choice(name=c, value=c)
            for c in options if current.lower() in c.lower()
        ][:25]

    @ping.command(name="remove", description="[Staff] Remove a role from the activity ping portal")
    @app_commands.describe(role="Discord role to remove")
    @requires_django_perm("org.manage_activity_ping_roles")
    async def ping_remove(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)

        ActivityPingRole = apps.get_model('org', 'ActivityPingRole')

        def _remove():
            return ActivityPingRole.objects.filter(discord_role_id=role.id).delete()

        deleted, _ = await sync_to_async(_remove)()
        if deleted:
            await interaction.followup.send(
                f"✅ Removed **{role.name}** from the ping portal.\n"
                "Run `/activity_ping refresh` to update the embed.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(f"❌ `{role.name}` was not in the ping portal.", ephemeral=True)

    # ------------------------------------------------------------------ #
    # /self_assign_roles                                                   #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="self_assign_roles",
        description="Add or remove roles from yourself using a selection menu.",
    )
    async def cmd_self_assign_roles(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        roles = await _fetch_active_roles()
        if not roles:
            await interaction.followup.send(
                "No self-assignable roles are configured yet. Check back later!",
                ephemeral=True,
            )
            return

        view = SelfAssignRolesView(interaction.user, roles)
        await interaction.followup.send(
            "**Select alert groups to join or leave:**\n"
            "Your current roles are pre-selected. Adjust and confirm each category.",
            view=view,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(ActivityPingCog(bot))
