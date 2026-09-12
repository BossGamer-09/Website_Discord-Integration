"""
app/disfunction/cogs/vc_generator.py

Perm-driven temporary voice channel generator.

Flow:
  member joins trigger channel
    → bot checks VoiceChannelProfile rows in sort_order
    → first profile whose required_permission the member holds wins
    → VC created with that profile's prefix/type
    → control panel + optional knights panel posted in VC
    → VoiceBanCog notified via DB signal → Redis pub/sub

No hardcoded role IDs or channel IDs.  All runtime config via preferences.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import discord
from django.apps import apps
from django.utils import timezone
from discord import app_commands
from discord.ext import commands, tasks
from asgiref.sync import sync_to_async

from app.preferences.utils import aget_global_preference
from app.main.util.discord_command_checks import requires_django_perm

BITRATE_MIN = 8_000
WARNING_REFRESH_DAYS = 30  # re-show the protected-channel warning after this many days


async def _member_in_group(discord_user_id: int, group_id: int) -> bool:
    """Returns True if the Discord user's linked Django user belongs to the given Group."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        return await User.objects.filter(
            discorduser__discorduid=discord_user_id,
            groups__id=group_id,
        ).aexists()
    except Exception:
        return False

# Discord allows 2 channel name edits per 10 minutes per channel.
# We enforce a conservative 5-minute cooldown per channel to stay safe.
_RENAME_COOLDOWN_SECS = 300


async def _discord_call_with_retry(coro, max_retries: int = 3, logger=None):
    """
    Awaits a Discord API coroutine, retrying on 429 rate-limit responses.
    Respects the retry_after value from the response.
    """
    for attempt in range(max_retries):
        try:
            return await coro
        except discord.HTTPException as e:
            if e.status == 429 and attempt < max_retries - 1:
                retry_after = getattr(e, "retry_after", 5.0) or 5.0
                if logger:
                    logger.warning("Rate limited (429), retrying in %.1fs (attempt %d/%d)", retry_after, attempt + 1, max_retries)
                await asyncio.sleep(retry_after)
            else:
                raise


# ------------------------------------------------------------------ #
# UI — Modals                                                          #
# ------------------------------------------------------------------ #

class RenameModal(discord.ui.Modal, title="Rename Voice Channel"):
    new_name = discord.ui.TextInput(label="New Channel Name", max_length=100, required=True)

    def __init__(self, cog, channel_id: int):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.rename_channel(interaction, self.channel_id, self.new_name.value)


class LimitModal(discord.ui.Modal, title="Set User Limit"):
    limit = discord.ui.TextInput(label="Max Users (0 = unlimited)", placeholder="0-99", max_length=2, required=True)

    def __init__(self, cog, channel_id: int):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.set_user_limit(interaction, self.channel_id, self.limit.value)


class BitrateModal(discord.ui.Modal, title="Set Bitrate"):
    bitrate = discord.ui.TextInput(label="Bitrate (kbps)", placeholder="8-384", max_length=4, required=True)

    def __init__(self, cog, channel_id: int):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.set_bitrate(interaction, self.channel_id, self.bitrate.value)


# ------------------------------------------------------------------ #
# UI — Views                                                           #
# ------------------------------------------------------------------ #

class VCControlView(discord.ui.View):
    def __init__(self, cog, channel_id: int, profile_slug: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.profile_slug = profile_slug

        select = discord.ui.Select(
            custom_id=f"vc_control:{channel_id}",
            placeholder="Select action…",
            options=[
                discord.SelectOption(label="Rename",           value="rename",    emoji="📝"),
                discord.SelectOption(label="User Limit",       value="limit",     emoji="👥"),
                discord.SelectOption(label="Bitrate",          value="bitrate",   emoji="🔊"),
                discord.SelectOption(label="Region",           value="region",    emoji="🌐"),
                discord.SelectOption(label="Switch Type",      value="switch",    emoji="🔄"),
                discord.SelectOption(label="Pause Delete (1h)",value="pause_1h",  emoji="⏸️"),
                discord.SelectOption(label="Pause Delete (3h)",value="pause_3h",  emoji="⏸️"),
                discord.SelectOption(label="Pause Delete (6h)",value="pause_6h",  emoji="⏸️"),
                discord.SelectOption(label="Resume Delete",    value="resume",    emoji="▶️"),
                discord.SelectOption(label="Kick User",        value="kick",      emoji="👢"),
                discord.SelectOption(label="Refresh Panel",    value="refresh",   emoji="🔃"),
            ],
        )
        select.callback = self._select_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        vc = interaction.guild.get_channel(self.channel_id)
        if not vc:
            await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
            return False
        if not interaction.user.voice or interaction.user.voice.channel != vc:
            await interaction.response.send_message("❌ You must be in the channel.", ephemeral=True)
            return False
        return True

    async def _select_callback(self, interaction: discord.Interaction):
        action = interaction.data["values"][0]
        try:
            if action == "rename":
                await interaction.response.send_modal(RenameModal(self.cog, self.channel_id))
            elif action == "limit":
                await interaction.response.send_modal(LimitModal(self.cog, self.channel_id))
            elif action == "bitrate":
                await interaction.response.send_modal(BitrateModal(self.cog, self.channel_id))
            elif action == "region":
                await interaction.response.defer(ephemeral=True)
                await self.cog.show_region_menu(interaction, self.channel_id)
            elif action == "switch":
                await interaction.response.defer(ephemeral=True)
                await self.cog.show_switch_menu(interaction, self.channel_id)
            elif action == "pause_1h":
                await interaction.response.defer(ephemeral=True)
                await self.cog.pause_deletion(interaction, self.channel_id, 1)
            elif action == "pause_3h":
                await interaction.response.defer(ephemeral=True)
                await self.cog.pause_deletion(interaction, self.channel_id, 3)
            elif action == "pause_6h":
                await interaction.response.defer(ephemeral=True)
                await self.cog.pause_deletion(interaction, self.channel_id, 6)
            elif action == "resume":
                await interaction.response.defer(ephemeral=True)
                await self.cog.resume_deletion(interaction, self.channel_id)
            elif action == "kick":
                await interaction.response.defer(ephemeral=True)
                await self.cog.show_kick_menu(interaction, self.channel_id)
            elif action == "refresh":
                await interaction.response.defer(ephemeral=True)
                await self.cog.refresh_control_panel(interaction, self.channel_id)
        except Exception:
            self.cog.logger.exception("VCControlView._select_callback error (action=%s)", action)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred.", ephemeral=True)
            else:
                await interaction.followup.send("❌ An error occurred.", ephemeral=True)


class RegionSelectView(discord.ui.View):
    def __init__(self, cog, channel_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.channel_id = channel_id

    @discord.ui.select(
        placeholder="Select region…",
        options=[
            discord.SelectOption(label="Automatic",    value="automatic"),
            discord.SelectOption(label="Brazil",       value="brazil"),
            discord.SelectOption(label="Hong Kong",    value="hongkong"),
            discord.SelectOption(label="India",        value="india"),
            discord.SelectOption(label="Japan",        value="japan"),
            discord.SelectOption(label="Rotterdam",    value="rotterdam"),
            discord.SelectOption(label="Singapore",    value="singapore"),
            discord.SelectOption(label="Sydney",       value="sydney"),
            discord.SelectOption(label="US East",      value="us-east"),
            discord.SelectOption(label="US West",      value="us-west"),
            discord.SelectOption(label="US Central",   value="us-central"),
            discord.SelectOption(label="US South",     value="us-south"),
        ],
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        await self.cog.set_region(interaction, self.channel_id, select.values[0])


class SwitchProfileView(discord.ui.View):
    def __init__(self, cog, channel_id: int, options: list[discord.SelectOption]):
        super().__init__(timeout=60)
        self.cog = cog
        self.channel_id = channel_id
        select = discord.ui.Select(placeholder="Switch to…", options=options)
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.switch_profile(interaction, self.channel_id, interaction.data["values"][0])


class KickUserView(discord.ui.View):
    def __init__(self, cog, channel_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.channel_id = channel_id
        sel = discord.ui.UserSelect(placeholder="Select user to kick…", max_values=1)
        sel.callback = self._callback
        self.add_item(sel)

    async def _callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        values = interaction.data.get("values", [])
        if values:
            member = interaction.guild.get_member(int(values[0]))
            if member:
                await self.cog.kick_user(interaction, self.channel_id, member)


class RedChannelWarningView(discord.ui.View):
    def __init__(self, client, guild_id: int):
        super().__init__(timeout=120)
        self.client = client
        self.guild_id = guild_id

    @discord.ui.button(label="I Understand", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def acknowledge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ Acknowledged.", embed=None, view=None)
        # Persist acknowledgement so the warning is suppressed for the next 30 days
        from app.disfunction.models import ProtectedChannelAck
        from django.utils import timezone as tz
        await ProtectedChannelAck.objects.aupdate_or_create(
            user_id=interaction.user.id,
            defaults={"last_acknowledged_at": tz.now()},
        )
        self.stop()

    @discord.ui.button(label="Leave Channel", style=discord.ButtonStyle.secondary, emoji="👋")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="👋 Leaving.", embed=None, view=None)
        guild = self.client.get_guild(self.guild_id)
        if guild:
            member = guild.get_member(interaction.user.id)
            if member and member.voice:
                await member.move_to(None, reason="User left protected channel")
        self.stop()


class KnightsControlView(discord.ui.View):
    """Persistent kick/ban/unban panel posted inside protected VCs."""

    def __init__(self, client, channel_id: int):
        super().__init__(timeout=None)
        self.client = client
        self.channel_id = channel_id

    async def _get_ban_cog(self):
        return self.client.cogs.get("VoiceBanCog")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Must be in the channel this panel belongs to
        if not interaction.user.voice or interaction.user.voice.channel.id != self.channel_id:
            await interaction.response.send_message("❌ You must be in this channel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.secondary, emoji="👢", row=0)
    async def kick_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select a user to **kick**:", view=_KnightsUserSelectView(self, "kick"), ephemeral=True
        )

    @discord.ui.button(label="Ban (60d)", style=discord.ButtonStyle.danger, emoji="🔨", row=0)
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select a user to **ban** (60 days):", view=_KnightsUserSelectView(self, "ban"), ephemeral=True
        )

    @discord.ui.button(label="Unban", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def unban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Select a user to **unban**:", view=_KnightsUserSelectView(self, "unban"), ephemeral=True
        )


class _KnightsUserSelectView(discord.ui.View):
    def __init__(self, parent: KnightsControlView, action: str):
        super().__init__(timeout=60)
        self.parent = parent
        self.action = action
        sel = discord.ui.UserSelect(placeholder="Select user…", max_values=1)
        sel.callback = self._callback
        self.add_item(sel)

    async def _callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        values = interaction.data.get("values", [])
        if not values:
            return
        guild = interaction.guild
        target = guild.get_member(int(values[0])) if guild else None
        if not target:
            await interaction.followup.send("❌ Member not found.", ephemeral=True)
            return

        ban_cog = await self.parent._get_ban_cog()

        if self.action == "kick":
            if target.voice:
                await target.move_to(None, reason=f"Kicked by Knight {interaction.user}")
                await interaction.followup.send(f"✅ Kicked {target.mention}.", ephemeral=True)
            else:
                await interaction.followup.send("❌ User is not in a voice channel.", ephemeral=True)

        elif self.action == "ban":
            if not ban_cog:
                await interaction.followup.send("❌ VoiceBanCog not loaded.", ephemeral=True)
                return
            success = await ban_cog.ban_user(target, invoker=interaction.user)
            if success:
                await interaction.followup.send(f"✅ {target.mention} banned from protected VCs for 60 days.", ephemeral=True)
            else:
                await interaction.followup.send(f"⚠️ {target.mention} already has an active ban.", ephemeral=True)

        elif self.action == "unban":
            if not ban_cog:
                await interaction.followup.send("❌ VoiceBanCog not loaded.", ephemeral=True)
                return
            success = await ban_cog.unban_user(target, invoker=interaction.user)
            if success:
                await interaction.followup.send(f"✅ {target.mention} unbanned.", ephemeral=True)
            else:
                await interaction.followup.send(f"⚠️ {target.mention} has no active ban.", ephemeral=True)


# ------------------------------------------------------------------ #
# Main Cog                                                             #
# ------------------------------------------------------------------ #

class VCGeneratorCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.TempVC = apps.get_model("disfunction", "TemporaryVoiceChannel")
        self.Profile = apps.get_model("disfunction", "VoiceChannelProfile")

        # channel_id → {"profile_slug", "creator_id", ...}
        self._active: dict[int, dict] = {}
        # user_id → timestamp of last join attempt (rate limit); capped at 500 entries
        self._last_join: dict[int, float] = {}
        # users that have already seen/dismissed the warning DM this session; capped at 500
        self._seen_warning: set[int] = set()
        # channel_id → monotonic timestamp of last rename (Discord allows 2/10min)
        self._last_rename: dict[int, float] = {}
        self._MAX_TRACKING = 500

        self.vc_cleanup.start()

    def cog_unload(self):
        self.vc_cleanup.cancel()

    async def cog_load(self):
        await self._restore_active_vcs()

    # ------------------------------------------------------------------ #
    # Startup restore                                                      #
    # ------------------------------------------------------------------ #

    async def _restore_active_vcs(self):
        try:
            stale_ids = []
            async for vc in self.TempVC.objects.filter(is_active=True):
                # Guard: channel may have been deleted while bot was offline
                discord_channel = self.client.get_channel(vc.channel_id)
                if discord_channel is None:
                    try:
                        discord_channel = await self.client.fetch_channel(vc.channel_id)
                    except (discord.NotFound, discord.Forbidden):
                        self.logger.info("VC %s deleted while offline, deactivating DB record", vc.channel_id)
                        stale_ids.append(vc.channel_id)
                        continue

                self._active[vc.channel_id] = {
                    "guild_id": vc.guild_id,
                    "parent_id": vc.parent_channel_id,
                    "creator_id": vc.creator_id,
                    "name": vc.name,
                    "profile_slug": vc.profile_slug,
                    "channel_type": vc.channel_type,
                    "control_message_id": vc.control_message_id,
                    "knights_message_id": None,
                    "is_paused": vc.is_paused,
                    "pause_until": vc.pause_until,
                }
                # Re-register the persistent view so the dropdown keeps working after restart
                if vc.control_message_id:
                    view = VCControlView(self, vc.channel_id, vc.profile_slug or "")
                    self.client.add_view(view, message_id=vc.control_message_id)
                self.logger.info("Restored VC from DB: %s (%s)", vc.name, vc.channel_type)

            if stale_ids:
                await self.TempVC.objects.filter(channel_id__in=stale_ids).aupdate(is_active=False)
        except Exception:
            self.logger.exception("Error restoring active VCs")

    # ------------------------------------------------------------------ #
    # Preference helpers                                                   #
    # ------------------------------------------------------------------ #

    async def _parent_map(self) -> dict[int, str | None]:
        """
        Returns {channel_id: forced_channel_class_or_None}.
        Main parent → None (use role-based sort-order resolution).
        Staff/Leader parents → force that channel class directly.
        """
        from app.disfunction.preferences import VCParentMainID, VCParentStaffID, VCParentLeaderID
        mapping = {}
        for pref_class, forced_class in (
            (VCParentMainID,   None),
            (VCParentStaffID,  "STAFF"),
            (VCParentLeaderID, "LEADER"),
        ):
            try:
                val = await aget_global_preference(pref_class.import_path)
                if val:
                    mapping[int(val)] = forced_class
            except Exception:
                pass
        return mapping

    async def _max_bitrate(self) -> int:
        from app.disfunction.preferences import VCBitrateMax
        try:
            return int(await aget_global_preference(VCBitrateMax.import_path))
        except Exception:
            return 96_000

    async def _cleanup_delay(self) -> int:
        from app.disfunction.preferences import VCCleanupDelaySecs
        try:
            return int(await aget_global_preference(VCCleanupDelaySecs.import_path))
        except Exception:
            return 10

    # ------------------------------------------------------------------ #
    # Profile resolution                                                   #
    # ------------------------------------------------------------------ #

    async def _resolve_profile(self, member: discord.Member):
        """
        Returns the first active PUBLIC/PROTECTED profile the member qualifies for.
        STAFF/LEADER profiles are only used via their dedicated parent channels (forced_class).
        """
        @sync_to_async
        def _fetch_profiles():
            return list(
                self.Profile.objects.filter(
                    is_active=True,
                    channel_class__in=["PUBLIC", "PROTECTED"],
                ).order_by("sort_order")
            )

        profiles = await _fetch_profiles()
        member_role_ids = {r.id for r in member.roles}

        self.logger.info(
            "_resolve_profile: user=%s profiles=%d member_roles=%s",
            member.id, len(profiles), member_role_ids,
        )

        for profile in profiles:
            if profile.required_group_id is None:
                self.logger.info("_resolve_profile: user=%s matched %s (open)", member.id, profile.slug)
                return profile
            if await _member_in_group(member.id, profile.required_group_id):
                self.logger.info("_resolve_profile: user=%s matched %s (group)", member.id, profile.slug)
                return profile

        return None

    # ------------------------------------------------------------------ #
    # Voice state events                                                   #
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        # Member joined a channel
        if after.channel and after.channel.id != (before.channel.id if before.channel else None):
            await self._on_join(member, after.channel)

        # Member left a channel
        if before.channel and before.channel.id != (after.channel.id if after.channel else None):
            await self._on_leave(member, before.channel)

    async def _on_join(self, member: discord.Member, channel: discord.VoiceChannel):
        parent_map = await self._parent_map()
        self.logger.info("_on_join: user=%s channel=%s parent_map=%s", member.id, channel.id, parent_map)
        if channel.id not in parent_map:
            # Not a trigger channel — check red-channel warning
            if channel.id in self._active:
                vc_data = self._active[channel.id]
                profile = await self._get_profile_by_slug(vc_data.get("profile_slug"))
                if (
                    profile
                    and profile.show_red_warning
                    and member.id not in self._seen_warning
                ):
                    # has_group_access: members with the required group don't need the warning
                    has_access = (
                        profile.required_group_id is None
                        or await _member_in_group(member.id, profile.required_group_id)
                    )
                    if not has_access:
                        if len(self._seen_warning) >= self._MAX_TRACKING:
                            self._seen_warning.pop()
                        self._seen_warning.add(member.id)  # in-session dedup guard
                        asyncio.create_task(self._send_warning_dm(member, channel.id))
            return

        # Rate limit: one VC every 2s per user
        now = time.monotonic()
        if now - self._last_join.get(member.id, 0) < 2:
            return
        if len(self._last_join) >= self._MAX_TRACKING:
            oldest = min(self._last_join, key=self._last_join.__getitem__)
            del self._last_join[oldest]
        self._last_join[member.id] = now

        forced_class = parent_map[channel.id]
        try:
            await self._create_vc(member, channel, forced_class=forced_class)
        except Exception:
            self.logger.exception("_create_vc failed for %s in %s", member.id, channel.id)

    async def _on_leave(self, member: discord.Member, channel: discord.VoiceChannel):
        if channel.id not in self._active:
            return

        # Red-warning DM cleanup
        self._seen_warning.discard(member.id)

        # Schedule deletion if empty
        asyncio.create_task(self._maybe_delete_vc(channel))

    # ------------------------------------------------------------------ #
    # VC Creation                                                          #
    # ------------------------------------------------------------------ #

    async def _overwrites_for_profile(self, profile, fallback: discord.VoiceChannel) -> dict:
        """Returns permission overwrites to apply for a profile.

        Uses VCRolePermission rows if configured; otherwise inherits from the trigger channel.
        """
        from app.disfunction.models import VCRolePermission
        rows = await sync_to_async(list)(VCRolePermission.objects.filter(profile=profile))
        if rows:
            guild = fallback.guild
            overwrites = {}
            for row in rows:
                if row.discord_role_id == 0:
                    target = guild.default_role
                else:
                    target = guild.get_role(row.discord_role_id)
                if target is None:
                    continue
                overwrites[target] = discord.PermissionOverwrite(**row.to_discord_overwrite(locked=False))
            return overwrites
        return dict(fallback.overwrites)

    async def _create_vc(self, member: discord.Member, parent: discord.VoiceChannel, forced_class: str | None = None):
        if forced_class:
            profile = await sync_to_async(
                lambda: self.Profile.objects.filter(is_active=True, channel_class=forced_class).order_by("sort_order").first()
            )()
        else:
            profile = await self._resolve_profile(member)

        if profile is None:
            self.logger.warning(
                "No profile matched for %s (id=%s) forced_class=%s",
                member.display_name, member.id, forced_class,
            )
            return

        guild = parent.guild
        count = await self.TempVC.objects.filter(parent_channel_id=parent.id, is_active=True).acount()
        channel_name = f"{profile.prefix}{count + 1}"

        max_bitrate = min(guild.bitrate_limit, await self._max_bitrate())

        vc = await _discord_call_with_retry(
            guild.create_voice_channel(
                name=channel_name,
                category=parent.category,
                bitrate=max_bitrate,
                user_limit=0,
                position=parent.position + 1,
                overwrites=await self._overwrites_for_profile(profile, parent),
                reason=f"VCGenerator: {profile.name} for {member}",
            ),
            logger=self.logger,
        )

        await _discord_call_with_retry(
            member.move_to(vc, reason="VCGenerator: moved to new VC"),
            logger=self.logger,
        )

        db_record = await self.TempVC.objects.acreate(
            channel_id=vc.id,
            guild_id=guild.id,
            parent_channel_id=parent.id,
            creator_id=member.id,
            name=channel_name,
            profile_slug=profile.slug,
            channel_type=profile.channel_class,
            user_limit=0,
            bitrate=max_bitrate,
            region="automatic",
            is_active=True,
        )

        self._active[vc.id] = {
            "guild_id": guild.id,
            "parent_id": parent.id,
            "creator_id": member.id,
            "name": channel_name,
            "profile_slug": profile.slug,
            "channel_type": profile.channel_class,
            "control_message_id": None,
            "knights_message_id": None,
            "is_paused": False,
            "pause_until": None,
        }

        asyncio.create_task(self._post_control_panel(vc, profile, member))

        # Apply all active voice bans immediately if this is a protected channel type
        if profile.apply_voice_ban:
            ban_cog = self.client.cogs.get("VoiceBanCog")
            if ban_cog:
                asyncio.create_task(ban_cog.apply_all_bans_to_channel(vc))

        self.logger.info("Created %s VC '%s' for %s", profile.channel_class, channel_name, member.display_name)

    # ------------------------------------------------------------------ #
    # Control panel                                                        #
    # ------------------------------------------------------------------ #

    async def _post_control_panel(
        self, vc: discord.VoiceChannel, profile, creator: discord.Member
    ):
        color_map = {
            "PUBLIC":    discord.Color.green(),
            "PROTECTED": discord.Color.blue(),
            "STAFF":     discord.Color.gold(),
            "LEADER":    discord.Color.purple(),
            "CUSTOM":    discord.Color.og_blurple(),
            # legacy
            "RED":  discord.Color.red(),
            "BLUE": discord.Color.blue(),
        }

        vc_data = self._active.get(vc.id, {})
        is_paused = vc_data.get("is_paused", False)
        pause_until = vc_data.get("pause_until")

        if is_paused and pause_until and timezone.now() < pause_until:
            ts = int(pause_until.timestamp())
            pause_str = f"⏸️ **Paused** — expires <t:{ts}:R>"
        else:
            pause_str = "▶️ Auto-delete active"

        embed = discord.Embed(
            title=f"🎛️ {vc.name} Control Panel",
            color=color_map.get(profile.channel_class, discord.Color.blurple()),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(
            name="📊 Settings",
            value=(
                f"**Type:** {profile.name}\n"
                f"**Users:** {len(vc.members)}/{vc.user_limit or '∞'}\n"
                f"**Bitrate:** {vc.bitrate // 1000} kbps\n"
                f"**Region:** {vc.rtc_region or 'Automatic'}\n"
                f"**Creator:** {creator.mention}\n"
                f"{pause_str}"
            ),
            inline=True,
        )
        embed.add_field(
            name="⚙️ Controls",
            value="Use the dropdown to rename, adjust limits, switch type, pause deletion, or kick users.",
            inline=True,
        )
        embed.set_footer(text=f"Channel ID: {vc.id}")

        # Delete old panel if it exists
        old_msg_id = self._active.get(vc.id, {}).get("control_message_id")
        if old_msg_id:
            try:
                old_msg = await vc.fetch_message(old_msg_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        view = VCControlView(self, vc.id, profile.slug)
        try:
            msg = await vc.send(embed=embed, view=view)
            self._active[vc.id]["control_message_id"] = msg.id
            await self.TempVC.objects.filter(channel_id=vc.id).aupdate(control_message_id=msg.id)
        except discord.Forbidden:
            self.logger.warning("Cannot send control panel in VC %s", vc.id)

        if profile.allow_knights_controls:
            await self._post_knights_panel(vc)
        else:
            # If profile no longer requires knights panel, delete any existing one
            old_knights_id = self._active.get(vc.id, {}).get("knights_message_id")
            if old_knights_id:
                try:
                    old_msg = await vc.fetch_message(old_knights_id)
                    await old_msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
                self._active[vc.id]["knights_message_id"] = None

    async def _post_knights_panel(self, vc: discord.VoiceChannel):
        # Delete old knights panel if exists
        old_knights_id = self._active.get(vc.id, {}).get("knights_message_id")
        if old_knights_id:
            try:
                old_msg = await vc.fetch_message(old_knights_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        embed = discord.Embed(
            title="⚔️ Knights Control Panel",
            description=(
                "**Kick** — remove a user from the channel.\n"
                "**Ban** — 60-day voice ban from all protected channels.\n"
                "**Unban** — lift an active voice ban."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Only Knights may use these controls.")
        view = KnightsControlView(self.client, vc.id)
        try:
            msg = await vc.send(embed=embed, view=view)
            if vc.id in self._active:
                self._active[vc.id]["knights_message_id"] = msg.id
        except discord.Forbidden:
            pass

    # ------------------------------------------------------------------ #
    # Warning DM                                                           #
    # ------------------------------------------------------------------ #

    async def _send_warning_dm(self, member: discord.Member, channel_id: int):
        from app.disfunction.models import ProtectedChannelAck
        from django.utils import timezone as tz
        from datetime import timedelta

        # Check DB: suppress if acknowledged within the last WARNING_REFRESH_DAYS days
        cutoff = tz.now() - timedelta(days=WARNING_REFRESH_DAYS)
        already_acked = await ProtectedChannelAck.objects.filter(
            user_id=member.id,
            last_acknowledged_at__gte=cutoff,
        ).aexists()
        if already_acked:
            return

        is_refresher = await ProtectedChannelAck.objects.filter(user_id=member.id).aexists()
        description = (
            "You have joined a **protected voice channel**.\n\n"
            "Feedback in this channel is direct and unfiltered. "
            "If you are not comfortable with that, please leave."
        )
        if is_refresher:
            description = (
                "**Monthly Reminder** — You have joined a **protected voice channel**.\n\n"
                "Feedback in this channel is direct and unfiltered. "
                "If you are not comfortable with that, please leave."
            )

        embed = discord.Embed(
            title="⚠️ Protected Channel Warning",
            description=description,
            color=discord.Color.red(),
        )
        view = RedChannelWarningView(self.client, member.guild.id)
        try:
            await member.send(embed=embed, view=view)
        except discord.Forbidden:
            pass

    # ------------------------------------------------------------------ #
    # VC Cleanup                                                           #
    # ------------------------------------------------------------------ #

    async def _maybe_delete_vc(self, channel: discord.VoiceChannel):
        delay = await self._cleanup_delay()
        await asyncio.sleep(delay)

        vc_data = self._active.get(channel.id)
        if vc_data and vc_data.get("is_paused"):
            pause_until = vc_data.get("pause_until")
            if pause_until and timezone.now() < pause_until:
                return

        # Refetch live channel
        live_ch = self.client.get_channel(channel.id)
        if live_ch and len(live_ch.members) > 0:
            return  # still occupied

        await self._delete_vc(channel.id)

    async def _save_transcript(self, ch: discord.VoiceChannel):
        """Fetch message history from a VC's text chat and persist it before deletion."""
        try:
            vc_record = await self.TempVC.objects.filter(channel_id=ch.id).afirst()
            if vc_record is None:
                return

            VCTranscript = apps.get_model('disfunction', 'VCTranscript')
            VCTranscriptMessage = apps.get_model('disfunction', 'VCTranscriptMessage')

            transcript = await sync_to_async(VCTranscript.objects.create)(
                channel_id=ch.id,
                channel_name=ch.name,
                guild_id=ch.guild.id,
                creator_id=vc_record.creator_id,
                profile_slug=vc_record.profile_slug,
                channel_type=vc_record.channel_type,
                vc_created_at=vc_record.created_at,
            )

            msg_objs = []
            try:
                async for msg in ch.history(limit=2000, oldest_first=True):
                    msg_objs.append(VCTranscriptMessage(
                        transcript=transcript,
                        message_id=msg.id,
                        author_id=msg.author.id,
                        author_name=str(msg.author.display_name),
                        author_avatar_url=str(msg.author.display_avatar.url) if msg.author.display_avatar else None,
                        is_bot=msg.author.bot,
                        content=msg.content,
                        attachments=[
                            {
                                'url': a.url,
                                'proxy_url': a.proxy_url,
                                'filename': a.filename,
                                'size': a.size,
                                'content_type': a.content_type or '',
                                'width': a.width,
                                'height': a.height,
                            }
                            for a in msg.attachments
                        ],
                        embeds_count=len(msg.embeds),
                        created_at=msg.created_at,
                    ))
            except discord.Forbidden:
                self.logger.warning("No read-history permission for VC %s — transcript skipped", ch.id)
            except Exception:
                self.logger.exception("Error fetching message history for VC %s", ch.id)

            if msg_objs:
                await sync_to_async(VCTranscriptMessage.objects.bulk_create)(msg_objs)

            await sync_to_async(
                lambda: VCTranscript.objects.filter(pk=transcript.pk).update(message_count=len(msg_objs))
            )()
            self.logger.info("Saved transcript for VC %s (%d messages)", ch.id, len(msg_objs))
        except Exception:
            self.logger.exception("Failed to save transcript for VC %s", ch.id)

    async def _delete_vc(self, channel_id: int):
        self._active.pop(channel_id, None)
        try:
            await self.TempVC.objects.filter(channel_id=channel_id).aupdate(is_active=False)
        except Exception:
            self.logger.exception("DB update failed for deleted VC %s", channel_id)

        ch = self.client.get_channel(channel_id)
        if ch:
            await self._save_transcript(ch)
            try:
                await _discord_call_with_retry(ch.delete(reason="VCGenerator: empty temp VC"), logger=self.logger)
                self.logger.info("Deleted empty VC %s", channel_id)
            except discord.NotFound:
                pass
            except Exception:
                self.logger.exception("Error deleting VC %s", channel_id)

    @tasks.loop(seconds=30)
    async def vc_cleanup(self):
        """Sweep for orphaned active DB records whose Discord channels no longer exist,
        and delete paused VCs whose timer has expired and are now empty."""
        to_deactivate = []
        expired_pauses = []

        async for record in self.TempVC.objects.filter(is_active=True):
            ch = self.client.get_channel(record.channel_id)
            if ch is None:
                to_deactivate.append(record.channel_id)
                self._active.pop(record.channel_id, None)
            elif record.is_paused and record.pause_until and timezone.now() >= record.pause_until:
                if len(ch.members) == 0:
                    expired_pauses.append(record.channel_id)

        if to_deactivate:
            await self.TempVC.objects.filter(channel_id__in=to_deactivate).aupdate(is_active=False)
            self.logger.info("Swept %d orphaned VC records", len(to_deactivate))

        for channel_id in expired_pauses:
            self.logger.info("Deleting VC %s — pause expired and channel is empty", channel_id)
            await self._delete_vc(channel_id)

    @vc_cleanup.before_loop
    async def _before_cleanup(self):
        await self.client.wait_until_ready()

    # ------------------------------------------------------------------ #
    # Control panel actions                                                #
    # ------------------------------------------------------------------ #

    async def _get_profile_by_slug(self, slug: Optional[str]):
        if not slug:
            return None
        try:
            return await self.Profile.objects.aget(slug=slug)
        except self.Profile.DoesNotExist:
            return None

    def _is_creator(self, interaction: discord.Interaction, channel_id: int) -> bool:
        vc_data = self._active.get(channel_id, {})
        return interaction.user.id == vc_data.get("creator_id")

    async def _is_creator_or_manager(self, interaction: discord.Interaction, channel_id: int) -> bool:
        if self._is_creator(interaction, channel_id):
            return True
        from app.main.util.discord_command_checks import _verify_and_cache_generic_permission, MissingDjangoPermission, AccountNotLinked
        try:
            return await _verify_and_cache_generic_permission(interaction.user.id, "disfunction.can_manage_temp_vcs")
        except (MissingDjangoPermission, AccountNotLinked):
            return False

    async def rename_channel(self, interaction: discord.Interaction, channel_id: int, new_name: str):
        if not await self._is_creator_or_manager(interaction, channel_id):
            await interaction.followup.send("❌ Only the creator or a VC manager can rename this channel.", ephemeral=True)
            return
        vc = interaction.guild.get_channel(channel_id)
        if not vc:
            await interaction.followup.send("❌ Channel not found.", ephemeral=True)
            return

        # Preserve the profile prefix
        prefix = ""
        if channel_id in self._active:
            profile = await self._get_profile_by_slug(self._active[channel_id].get("profile_slug"))
            if profile and profile.prefix:
                prefix = profile.prefix

        full_name = (prefix + new_name)[:100]

        now = time.monotonic()
        last = self._last_rename.get(channel_id, 0)
        if now - last < _RENAME_COOLDOWN_SECS:
            remaining = int(_RENAME_COOLDOWN_SECS - (now - last))
            await interaction.followup.send(
                f"⏳ Rename is on cooldown — try again in **{remaining}s**. "
                "(Discord limits channel renames to 2 per 10 minutes.)",
                ephemeral=True,
            )
            return

        if len(self._last_rename) >= self._MAX_TRACKING:
            oldest = min(self._last_rename, key=self._last_rename.__getitem__)
            del self._last_rename[oldest]
        self._last_rename[channel_id] = now
        await _discord_call_with_retry(
            vc.edit(name=full_name, reason=f"Renamed by {interaction.user}"),
            logger=self.logger,
        )
        await self.TempVC.objects.filter(channel_id=channel_id).aupdate(name=full_name)
        if channel_id in self._active:
            self._active[channel_id]["name"] = full_name
        await interaction.followup.send(f"✅ Renamed to **{full_name}**", ephemeral=True)

    async def set_user_limit(self, interaction: discord.Interaction, channel_id: int, value: str):
        try:
            limit = max(0, min(99, int(value)))
        except ValueError:
            await interaction.followup.send("❌ Invalid number.", ephemeral=True)
            return
        vc = interaction.guild.get_channel(channel_id)
        if not vc:
            await interaction.followup.send("❌ Channel not found.", ephemeral=True)
            return
        await _discord_call_with_retry(vc.edit(user_limit=limit, reason=f"Limit set by {interaction.user}"), logger=self.logger)
        await self.TempVC.objects.filter(channel_id=channel_id).aupdate(user_limit=limit)
        await interaction.followup.send(f"✅ User limit set to **{limit or '∞'}**", ephemeral=True)

    async def set_bitrate(self, interaction: discord.Interaction, channel_id: int, value: str):
        try:
            kbps = int(value)
        except ValueError:
            await interaction.followup.send("❌ Invalid number.", ephemeral=True)
            return
        max_kbps = (await self._max_bitrate()) // 1000
        kbps = max(8, min(max_kbps, kbps))
        vc = interaction.guild.get_channel(channel_id)
        if not vc:
            await interaction.followup.send("❌ Channel not found.", ephemeral=True)
            return
        await _discord_call_with_retry(vc.edit(bitrate=kbps * 1000, reason=f"Bitrate set by {interaction.user}"), logger=self.logger)
        await self.TempVC.objects.filter(channel_id=channel_id).aupdate(bitrate=kbps * 1000)
        await interaction.followup.send(f"✅ Bitrate set to **{kbps} kbps**", ephemeral=True)

    async def set_region(self, interaction: discord.Interaction, channel_id: int, region: str):
        vc = interaction.guild.get_channel(channel_id)
        if not vc:
            await interaction.followup.send("❌ Channel not found.", ephemeral=True)
            return
        rtc_region = None if region == "automatic" else region
        await _discord_call_with_retry(vc.edit(rtc_region=rtc_region, reason=f"Region set by {interaction.user}"), logger=self.logger)
        await self.TempVC.objects.filter(channel_id=channel_id).aupdate(region=region)
        await interaction.followup.send(f"✅ Region set to **{region}**", ephemeral=True)

    async def show_region_menu(self, interaction: discord.Interaction, channel_id: int):
        await interaction.followup.send(view=RegionSelectView(self, channel_id), ephemeral=True)

    async def show_switch_menu(self, interaction: discord.Interaction, channel_id: int):
        vc_data = self._active.get(channel_id, {})
        profile = await self._get_profile_by_slug(vc_data.get("profile_slug"))
        if profile and not profile.allow_type_switch:
            await interaction.followup.send("❌ This channel type cannot be switched.", ephemeral=True)
            return

        @sync_to_async
        def _get_switchable():
            return list(
                self.Profile.objects.filter(
                    is_active=True,
                    channel_class__in=["PUBLIC", "PROTECTED", "CUSTOM"],
                ).order_by("sort_order")
            )

        all_profiles = await _get_switchable()

        # Filter to only profiles this user can access
        options = []
        for p in all_profiles:
            if p.slug == vc_data.get("profile_slug"):
                continue
            if p.required_group_id is not None and not await _member_in_group(interaction.user.id, p.required_group_id):
                continue
            options.append(discord.SelectOption(label=p.name, value=p.slug, description=p.channel_class))
        if not options:
            await interaction.followup.send("❌ No other profiles available to switch to.", ephemeral=True)
            return
        await interaction.followup.send(view=SwitchProfileView(self, channel_id, options[:25]), ephemeral=True)

    async def switch_profile(self, interaction: discord.Interaction, channel_id: int, new_slug: str):
        if not await self._is_creator_or_manager(interaction, channel_id):
            await interaction.followup.send("❌ Only the creator or a VC manager can switch type.", ephemeral=True)
            return
        try:
            new_profile = await self.Profile.objects.aget(slug=new_slug, is_active=True)
        except self.Profile.DoesNotExist:
            await interaction.followup.send("❌ Profile not found.", ephemeral=True)
            return

        # Verify the user still has the required group for the target profile
        if new_profile.required_group_id is not None:
            if not await _member_in_group(interaction.user.id, new_profile.required_group_id):
                await interaction.followup.send("❌ You don't have permission to switch to that channel type.", ephemeral=True)
                return

        vc = interaction.guild.get_channel(channel_id)
        if not vc:
            await interaction.followup.send("❌ Channel not found.", ephemeral=True)
            return

        # Preserve the user-given name (strip old prefix, apply new one)
        old_name = self._active.get(channel_id, {}).get("name", vc.name)
        old_profile = await self._get_profile_by_slug(self._active.get(channel_id, {}).get("profile_slug"))
        base_name = old_name
        if old_profile and old_profile.prefix and base_name.startswith(old_profile.prefix):
            base_name = base_name[len(old_profile.prefix):]
        new_name = (new_profile.prefix + base_name)[:100]

        # Inherit overwrites from profile source or parent channel
        parent_id = self._active.get(channel_id, {}).get("parent_id")
        parent = interaction.guild.get_channel(parent_id) if parent_id else vc
        new_overwrites = await self._overwrites_for_profile(new_profile, parent)

        self._last_rename[channel_id] = time.monotonic()
        await _discord_call_with_retry(
            vc.edit(
                name=new_name,
                overwrites=new_overwrites,
                reason=f"Profile switched to {new_profile.name} by {interaction.user}",
            ),
            logger=self.logger,
        )

        await self.TempVC.objects.filter(channel_id=channel_id).aupdate(
            profile_slug=new_profile.slug,
            channel_type=new_profile.channel_class,
            name=new_name,
        )
        if channel_id in self._active:
            self._active[channel_id]["profile_slug"] = new_profile.slug
            self._active[channel_id]["channel_type"] = new_profile.channel_class
            self._active[channel_id]["name"] = new_name

        await interaction.followup.send(f"✅ Switched to **{new_profile.name}**", ephemeral=True)

        # Redraw the control panel with the new profile
        creator = interaction.guild.get_member(self._active.get(channel_id, {}).get("creator_id", 0))
        asyncio.create_task(self._post_control_panel(vc, new_profile, creator or interaction.user))

    async def pause_deletion(self, interaction: discord.Interaction, channel_id: int, hours: int):
        if not await self._is_creator_or_manager(interaction, channel_id):
            await interaction.followup.send("❌ Only the creator or a VC manager can pause deletion.", ephemeral=True)
            return
        until = timezone.now() + timedelta(hours=hours)
        await self.TempVC.objects.filter(channel_id=channel_id).aupdate(is_paused=True, pause_until=until)
        if channel_id in self._active:
            self._active[channel_id]["is_paused"] = True
            self._active[channel_id]["pause_until"] = until
        await interaction.followup.send(f"⏸️ Auto-delete paused for **{hours}h**", ephemeral=True)
        await self._refresh_panel_silent(interaction.guild, channel_id)

    async def resume_deletion(self, interaction: discord.Interaction, channel_id: int):
        if not await self._is_creator_or_manager(interaction, channel_id):
            await interaction.followup.send("❌ Only the creator or a VC manager can resume deletion.", ephemeral=True)
            return
        await self.TempVC.objects.filter(channel_id=channel_id).aupdate(is_paused=False, pause_until=None)
        if channel_id in self._active:
            self._active[channel_id]["is_paused"] = False
            self._active[channel_id]["pause_until"] = None
        await interaction.followup.send("▶️ Auto-delete resumed.", ephemeral=True)
        await self._refresh_panel_silent(interaction.guild, channel_id)

    async def _refresh_panel_silent(self, guild: discord.Guild, channel_id: int):
        """Silently rebuild the control panel embed after a state change."""
        vc = guild.get_channel(channel_id)
        if not vc:
            return
        vc_data = self._active.get(channel_id, {})
        profile = await self._get_profile_by_slug(vc_data.get("profile_slug"))
        if not profile:
            return
        creator = guild.get_member(vc_data.get("creator_id", 0))
        await self._post_control_panel(vc, profile, creator or guild.me)

    async def show_kick_menu(self, interaction: discord.Interaction, channel_id: int):
        if not await self._is_creator_or_manager(interaction, channel_id):
            await interaction.followup.send("❌ Only the creator or a VC manager can kick users.", ephemeral=True)
            return
        await interaction.followup.send(view=KickUserView(self, channel_id), ephemeral=True)

    async def kick_user(self, interaction: discord.Interaction, channel_id: int, target: discord.Member):
        vc = interaction.guild.get_channel(channel_id)
        if not vc or not target.voice or target.voice.channel != vc:
            await interaction.followup.send("❌ User is not in this channel.", ephemeral=True)
            return
        await target.move_to(None, reason=f"Kicked by {interaction.user} via VC control panel")
        await interaction.followup.send(f"👢 **{target.display_name}** kicked.", ephemeral=True)

    async def refresh_control_panel(self, interaction: discord.Interaction, channel_id: int):
        vc = interaction.guild.get_channel(channel_id)
        if not vc:
            await interaction.followup.send("❌ Channel not found.", ephemeral=True)
            return
        vc_data = self._active.get(channel_id, {})
        profile = await self._get_profile_by_slug(vc_data.get("profile_slug"))
        if profile:
            creator = interaction.guild.get_member(vc_data.get("creator_id", 0))
            await self._post_control_panel(vc, profile, creator or interaction.user)
        await interaction.followup.send("🔃 Panel refreshed.", ephemeral=True)

    # ------------------------------------------------------------------ #
    # Admin slash commands                                                 #
    # ------------------------------------------------------------------ #

    @app_commands.command(name="vc_info", description="Show info about the current temp VC")
    @requires_django_perm("disfunction.view_temporaryvoicechannel")
    async def cmd_vc_info(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You are not in a voice channel.", ephemeral=True)
            return
        channel_id = interaction.user.voice.channel.id
        vc_data = self._active.get(channel_id)
        if not vc_data:
            await interaction.response.send_message("❌ This is not a managed temp VC.", ephemeral=True)
            return
        embed = discord.Embed(title="📊 VC Info", color=discord.Color.blue())
        embed.add_field(name="Profile", value=vc_data.get("profile_slug") or "unknown", inline=True)
        embed.add_field(name="Type",    value=vc_data.get("channel_type") or "unknown", inline=True)
        embed.add_field(name="Creator", value=f"<@{vc_data.get('creator_id', 0)}>", inline=True)
        paused = vc_data.get("is_paused", False)
        embed.add_field(name="Auto-Delete", value="⏸️ Paused" if paused else "▶️ Active", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(client):
    await client.add_cog(VCGeneratorCog(client))
