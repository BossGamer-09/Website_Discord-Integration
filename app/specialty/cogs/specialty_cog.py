"""
app/specialty/cogs/specialty_cog.py

Specialty granting panel — ported from the legacy JS bot.

Flow:
  1. Staff posts a panel (/specialty panel) with a category select menu.
  2. Granter picks a category → ephemeral button row per specialty.
  3. Granter clicks a specialty button → ephemeral UserSelectMenu.
  4. Granter selects members → approval embed posted to approval channel.
  5. Approver clicks Approve/Deny → roles applied / embed updated.
"""
import json
import logging
import math

import discord
from discord import app_commands
from discord.ext import commands, tasks
from asgiref.sync import sync_to_async

from app.specialty.constants import (
    SPECIALTY_DATA,
    CATEGORY_PARENT_ROLES,
    COMBINATION_MAPPINGS,
    MULTI_SPECIALIST_ROLE,
    GRANTER_ALLOWED_ROLES,
    SPECIALTY_MENU_OPTIONS,
    CATEGORY_BUTTONS,
)
from app.specialty.signals import PUBSUB_CHANNEL
from app.preferences.utils import aget_global_preference
from app.specialty.preferences import (
    SpecialtyApprovalChannelID,
    SpecialtyAchievementsChannelID,
    SpecialtyGrantingEnabled,
)

log = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────────────

def _build_category_select() -> discord.ui.Select:
    options = [
        discord.SelectOption(label=label, value=value)
        for value, label in SPECIALTY_MENU_OPTIONS
    ]
    select = discord.ui.Select(
        custom_id="specialty_category_select",
        placeholder="Select a specialty category",
        options=options,
    )
    return select


def _build_specialty_buttons(category_key: str) -> list[discord.ui.ActionRow]:
    buttons_cfg = CATEGORY_BUTTONS.get(category_key, [])
    rows = []
    for i in range(0, len(buttons_cfg), 5):
        chunk = buttons_cfg[i:i+5]
        row = discord.ui.ActionRow(
            *[
                discord.ui.Button(
                    label=label,
                    custom_id=f"specialty_btn_{key}",
                    style=discord.ButtonStyle.secondary,
                )
                for key, label in chunk
            ]
        )
        rows.append(row)
    return rows


def _category_select_row() -> discord.ui.ActionRow:
    options = [
        discord.SelectOption(label=label, value=value)
        for value, label in SPECIALTY_MENU_OPTIONS
    ]
    return discord.ui.ActionRow(
        discord.ui.Select(
            custom_id="specialty_category_select",
            placeholder="Select a specialty category",
            options=options,
        )
    )


def _approval_components(member_id: int, specialty_key: str) -> list[discord.ui.ActionRow]:
    return [
        discord.ui.ActionRow(
            discord.ui.Button(
                label="Approve",
                custom_id=f"specialty_approve_{member_id}_{specialty_key}",
                style=discord.ButtonStyle.success,
            ),
            discord.ui.Button(
                label="Deny",
                custom_id=f"specialty_deny_{member_id}_{specialty_key}",
                style=discord.ButtonStyle.danger,
            ),
        )
    ]


async def _check_combinations(member: discord.Member) -> list[str]:
    """Return labels of combination specialties the member newly qualifies for."""
    newly = []
    for combo in COMBINATION_MAPPINGS:
        has_all    = all(member.roles.cache.has(int(r)) or any(role.id == int(r) for role in member.roles) for r in combo["required_roles"])
        has_result = any(role.id == int(combo["resulting_role"]) for role in member.roles)
        if has_all and not has_result:
            newly.append((combo["resulting_role"], combo["label"]))
    return newly


def _has_role(member: discord.Member, role_id: str) -> bool:
    return any(r.id == int(role_id) for r in member.roles)


async def _apply_specialty_roles(member: discord.Member, specialty_key: str):
    """
    Apply the specialty roles and handle the multi-specialist / parent-role logic.
    Returns list of (role_id, label) combination specialties newly granted.
    """
    spec = SPECIALTY_DATA[specialty_key]

    # Grant all specialty roles
    for role_id in spec["roles"]:
        role = member.guild.get_role(int(role_id))
        if role:
            await member.add_roles(role, reason=f"Specialty grant: {spec['label']}")

    # Combination check AFTER granting (roles now on member object — need refetch)
    # We check based on the updated local cache; the member was refetched before this call.
    newly_combo = []
    for combo in COMBINATION_MAPPINGS:
        has_all    = all(_has_role(member, r) or r in spec["roles"] for r in combo["required_roles"])
        has_result = _has_role(member, combo["resulting_role"])
        if has_all and not has_result:
            role = member.guild.get_role(int(combo["resulting_role"]))
            if role:
                await member.add_roles(role, reason=f"Combination specialty: {combo['label']}")
            newly_combo.append(combo["label"])

    # Multi-specialist / parent role logic
    existing_parents = [r for r in member.roles if str(r.id) in CATEGORY_PARENT_ROLES]
    has_multi        = _has_role(member, MULTI_SPECIALIST_ROLE)

    if has_multi:
        # Already multi-specialist: strip individual parent roles
        for r in existing_parents:
            await member.remove_roles(r, reason="Multi-specialist cleanup")
    else:
        current_parent_ids = {str(r.id) for r in existing_parents}
        new_parent         = next((rid for rid in spec["roles"] if rid in CATEGORY_PARENT_ROLES), None)
        all_parent_ids     = current_parent_ids | ({new_parent} if new_parent else set())

        if len(all_parent_ids) > 1:
            # Becomes multi-specialist
            for r in existing_parents:
                await member.remove_roles(r, reason="Upgrading to multi-specialist")
            multi_role = member.guild.get_role(int(MULTI_SPECIALIST_ROLE))
            if multi_role:
                await member.add_roles(multi_role, reason="Multi-specialist granted")
        elif new_parent and not _has_role(member, new_parent):
            role = member.guild.get_role(int(new_parent))
            if role:
                await member.add_roles(role, reason=f"Category parent: {spec['label']}")

    return newly_combo


# ── Cog ──────────────────────────────────────────────────────────────────────

class SpecialtyCog(commands.Cog):
    def __init__(self, client):
        self.client       = client
        self.redis_client = None
        self.pubsub       = None

    async def cog_load(self):
        from django.conf import settings as django_settings
        import redis.asyncio as redis
        pool              = redis.ConnectionPool.from_url(django_settings.CACHES["default"]["LOCATION"])
        self.redis_client = redis.Redis.from_pool(pool)
        self.pubsub       = self.redis_client.pubsub()
        await self.pubsub.subscribe(PUBSUB_CHANNEL)
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
            if not msg:
                return
            data   = json.loads(msg["data"])
            action = data.get("action")
            if action == "SPECIALTY_GRANT_APPROVED":
                await self._on_approved(data)
            elif action == "SPECIALTY_GRANT_DENIED":
                await self._on_denied(data)
        except Exception:
            log.exception("SpecialtyCog listener_loop error")

    @listener_loop.before_loop
    async def _before_listener(self):
        await self.client.wait_until_ready()

    # ── Slash command ─────────────────────────────────────────────────────

    @app_commands.command(name="specialty", description="Open the specialty granting panel.")
    @app_commands.guild_only()
    async def specialty_panel(self, interaction: discord.Interaction):
        enabled = await aget_global_preference(SpecialtyGrantingEnabled.import_path)
        if not enabled:
            await interaction.response.send_message("Specialty granting is currently disabled.", ephemeral=True)
            return

        if not any(str(r.id) in GRANTER_ALLOWED_ROLES for r in interaction.user.roles):
            await interaction.response.send_message("You do not have permission to use this panel.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Specialty Granting Panel",
            description="Select a category to begin.",
            color=discord.Color.blurple(),
        )
        view = _PanelView()
        await interaction.response.send_message(embed=embed, view=view)

    # ── Interaction handlers (called from on_interaction) ─────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        cid = getattr(interaction, "custom_id", None) or (
            interaction.data.get("custom_id") if interaction.data else None
        )
        if not cid:
            return

        if cid == "specialty_category_select":
            await self._handle_category_select(interaction)
        elif cid.startswith("specialty_btn_"):
            await self._handle_specialty_button(interaction)
        elif cid.startswith("specialty_usermenu_"):
            await self._handle_user_menu(interaction)
        elif cid.startswith("specialty_approve_") or cid.startswith("specialty_deny_"):
            await self._handle_approval(interaction)

    # ── Step 2: category selected → show specialty buttons ────────────────

    async def _handle_category_select(self, interaction: discord.Interaction):
        if not any(str(r.id) in GRANTER_ALLOWED_ROLES for r in interaction.user.roles):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        category_key = interaction.data["values"][0]
        buttons_cfg  = CATEGORY_BUTTONS.get(category_key, [])
        if not buttons_cfg:
            await interaction.response.send_message("Invalid category.", ephemeral=True)
            return

        await interaction.response.defer()

        title_map = dict(SPECIALTY_MENU_OPTIONS)
        embed = discord.Embed(
            title=title_map.get(category_key, "Select a Specialty"),
            color=discord.Color.blurple(),
        )

        # Rebuild the category dropdown
        view = _PanelView()

        # Build specialty buttons in chunks of 5
        chunks = [buttons_cfg[i:i+5] for i in range(0, len(buttons_cfg), 5)]
        for chunk in chunks:
            for key, label in chunk:
                view.add_item(
                    discord.ui.Button(
                        label=label,
                        custom_id=f"specialty_btn_{key}",
                        style=discord.ButtonStyle.secondary,
                        row=1 + chunks.index(chunk),
                    )
                )

        await interaction.followup.send(embeds=[embed], view=view, ephemeral=True)

    # ── Step 3: specialty button clicked → user select menu ──────────────

    async def _handle_specialty_button(self, interaction: discord.Interaction):
        specialty_key = interaction.data["custom_id"].removeprefix("specialty_btn_")
        spec = SPECIALTY_DATA.get(specialty_key)
        if not spec:
            await interaction.response.send_message("Invalid specialty.", ephemeral=True)
            return

        view = discord.ui.View()
        view.add_item(
            discord.ui.UserSelect(
                custom_id=f"specialty_usermenu_{specialty_key}",
                placeholder=f"Select members for {spec['label']}",
                min_values=1,
                max_values=25,
            )
        )
        await interaction.response.send_message(
            content=f"Select members to grant **{spec['label']}**:",
            view=view,
            ephemeral=True,
        )

    # ── Step 4: members selected → post approval embeds ──────────────────

    async def _handle_user_menu(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        specialty_key = interaction.data["custom_id"].removeprefix("specialty_usermenu_")
        spec = SPECIALTY_DATA.get(specialty_key)
        if not spec:
            await interaction.followup.send("Invalid specialty.", ephemeral=True)
            return

        approval_chan_id = int(await aget_global_preference(SpecialtyApprovalChannelID.import_path) or 0)
        if not approval_chan_id:
            await interaction.followup.send("Approval channel not configured. Ask an admin to set SpecialtyApprovalChannelID.", ephemeral=True)
            return

        approval_channel = interaction.guild.get_channel(approval_chan_id)
        if not approval_channel:
            try:
                approval_channel = await interaction.guild.fetch_channel(approval_chan_id)
            except discord.NotFound:
                await interaction.followup.send("Approval channel not found.", ephemeral=True)
                return

        selected_ids = interaction.data["values"]
        failed       = []

        for member_id in selected_ids:
            member = interaction.guild.get_member(int(member_id))
            if not member:
                try:
                    member = await interaction.guild.fetch_member(int(member_id))
                except discord.NotFound:
                    failed.append(member_id)
                    continue

            # Check for upcoming combination specialties
            combo_preview = []
            for combo in COMBINATION_MAPPINGS:
                spec_roles_set = set(spec["roles"])
                will_have_all = all(
                    _has_role(member, r) or r in spec_roles_set
                    for r in combo["required_roles"]
                )
                has_result = _has_role(member, combo["resulting_role"])
                if will_have_all and not has_result:
                    combo_preview.append(combo["label"])

            desc = (
                f"**Member:** {member.mention}\n"
                f"**Specialty:** {spec['label']}\n"
                f"**Requested By:** {interaction.user.mention}"
            )
            if combo_preview:
                desc += f"\n**Combination Specialties (New):** {', '.join(combo_preview)}"

            embed = discord.Embed(
                title="Specialty Grant Request",
                description=desc,
                color=discord.Color.blue(),
            )

            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(
                label="Approve",
                custom_id=f"specialty_approve_{member.id}_{specialty_key}",
                style=discord.ButtonStyle.success,
            ))
            view.add_item(discord.ui.Button(
                label="Deny",
                custom_id=f"specialty_deny_{member.id}_{specialty_key}",
                style=discord.ButtonStyle.danger,
            ))

            msg = await approval_channel.send(embed=embed, view=view)

            # Persist to DB
            await sync_to_async(self._create_grant_record)(
                member_id=member.id,
                requester_id=interaction.user.id,
                specialty_key=specialty_key,
                spec=spec,
                message_id=msg.id,
            )

        if failed:
            await interaction.followup.send(
                f"Could not process members: {', '.join(failed)}. Valid requests submitted.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("Grant request(s) submitted for approval.", ephemeral=True)

    def _create_grant_record(self, *, member_id, requester_id, specialty_key, spec, message_id):
        from app.specialty.models import SpecialtyGrant
        SpecialtyGrant.objects.create(
            recipient_discord_id=member_id,
            requested_by_discord_id=requester_id,
            specialty_key=specialty_key,
            specialty_label=spec["label"],
            category=spec["category"],
            approval_channel_message_id=message_id,
        )

    # ── Step 5: approve / deny ────────────────────────────────────────────

    async def _handle_approval(self, interaction: discord.Interaction):
        await interaction.response.defer()

        cid   = interaction.data["custom_id"]
        parts = cid.split("_")
        # custom_id format: specialty_approve_<member_id>_<specialty_key...>
        # parts[0]="specialty" parts[1]="approve"|"deny" parts[2]=member_id parts[3..]=key parts
        action      = parts[1]          # "approve" or "deny"
        member_id   = int(parts[2])
        specialty_key = "_".join(parts[3:])

        spec = SPECIALTY_DATA.get(specialty_key)
        if not spec:
            await interaction.followup.send("Invalid specialty.", ephemeral=True)
            return

        embed = discord.Embed.from_dict(interaction.message.embeds[0].to_dict()) if interaction.message.embeds else discord.Embed()

        if action == "approve":
            member = interaction.guild.get_member(member_id)
            if not member:
                try:
                    member = await interaction.guild.fetch_member(member_id)
                except discord.NotFound:
                    embed.color = discord.Color.orange()
                    embed.add_field(name="Status", value="Member not found in server.")
                    await interaction.edit_original_response(embed=embed, view=None)
                    return

            # Duplicate check
            if _has_role(member, spec["specific_role"]):
                embed.color = discord.Color.orange()
                embed.add_field(name="Status", value="Member already has this specialty.")
                await interaction.edit_original_response(embed=embed, view=None)
                return

            newly_combo = await _apply_specialty_roles(member, specialty_key)

            # Achievements channel announcement
            achievements_chan_id = int(await aget_global_preference(SpecialtyAchievementsChannelID.import_path) or 0)
            if achievements_chan_id:
                ach_channel = interaction.guild.get_channel(achievements_chan_id)
                if ach_channel:
                    training_link = spec.get("training_link")
                    training_txt  = f"\n\n**Training Material:** <#{training_link}>" if training_link else ""
                    combo_txt     = f"\n\n**Combination Specialties:** {', '.join(newly_combo)}" if newly_combo else ""

                    ann_embed = discord.Embed(
                        title=f"{spec['label']} Specialty Achieved",
                        description=f"{member.mention} has successfully acquired the **{spec['label']}** specialty.{training_txt}{combo_txt}",
                        color=0x04909C,
                    )
                    await ach_channel.send(content=member.mention, embed=ann_embed)

                    # Combo-specific announcements
                    for combo_label in newly_combo:
                        combo_embed = discord.Embed(
                            title=f"{combo_label} Specialty Achieved",
                            description=f"{member.mention} has successfully acquired the **{combo_label}** specialty.",
                            color=0x04909C,
                        )
                        await ach_channel.send(content=member.mention, embed=combo_embed)

            embed.color = discord.Color.green()
            embed.add_field(name="Status", value=f"Approved by {interaction.user.mention}")
            if newly_combo:
                embed.add_field(name="Combination Specialties Granted", value=", ".join(newly_combo), inline=False)

            await self._resolve_grant(member_id, specialty_key, "APPROVED", interaction.user.id)

        else:  # deny
            embed.color = discord.Color.red()
            embed.add_field(name="Status", value=f"Denied by {interaction.user.mention}")
            await self._resolve_grant(member_id, specialty_key, "DENIED", interaction.user.id)

        await interaction.edit_original_response(embed=embed, view=None)

    @sync_to_async
    def _resolve_grant(self, recipient_id, specialty_key, status, resolved_by_id):
        from app.specialty.models import SpecialtyGrant
        from django.utils import timezone as tz
        grant = (
            SpecialtyGrant.objects
            .filter(recipient_discord_id=recipient_id, specialty_key=specialty_key, status="PENDING")
            .order_by("-requested_at")
            .first()
        )
        if grant:
            grant.status                = status
            grant.resolved_by_discord_id = resolved_by_id
            grant.resolved_at           = tz.now()
            grant.save()

    # ── Redis event handlers (from Django-side saves) ─────────────────────

    async def _on_approved(self, data: dict):
        # The approval was handled directly in the cog; nothing extra needed here
        # unless you want to support web-admin approvals via Django admin later.
        pass

    async def _on_denied(self, data: dict):
        pass


# ── Panel view (persistent select) ───────────────────────────────────────────

class _PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        options = [
            discord.SelectOption(label=label, value=value)
            for value, label in SPECIALTY_MENU_OPTIONS
        ]
        self.add_item(
            discord.ui.Select(
                custom_id="specialty_category_select",
                placeholder="Select a specialty category",
                options=options,
                row=0,
            )
        )


async def setup(client):
    await client.add_cog(SpecialtyCog(client))
