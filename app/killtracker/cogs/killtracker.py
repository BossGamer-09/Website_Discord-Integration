"""KillTracker cog: Redis Pub/Sub listener + /killtracker slash commands."""
import asyncio
import json
import logging
from datetime import datetime, timezone as dt_tz

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands, tasks
from django.apps import apps
from redis import asyncio as aioredis
from django.conf import settings

from app.killtracker.game_data import (
    display_game_mode,
    display_ship,
    display_weapon,
    cleanup_name,
)
from app.killtracker.rsi_scraper import fetch_rsi_profile
from app.preferences.utils import aget_global_preference

log = logging.getLogger(__name__)

PUBSUB_CHANNEL = "killtracker.events"


async def _user_for_discord_id(discord_id: int):
    from app.discordauth.models import DiscordUser
    return await DiscordUser.objects.select_related("user").filter(discorduid=discord_id).afirst()


# Placeholder values the display helpers emit when the real data is missing. A field whose
# value is one of these (or empty) is omitted from the embed entirely — no blank/filler lines.
_EMPTY_FIELD_VALUES = {
    "", "unknown", "unknown ship", "unknown weapon", "na", "n/a",
    "none", "nothing", "no organization", "-", "—",
}


def _add_field(e: discord.Embed, name: str, value, inline: bool = True) -> None:
    if value is None or str(value).strip().lower() in _EMPTY_FIELD_VALUES:
        return
    e.add_field(name=name, value=value, inline=inline)


def _fmt_enlisted(raw: str) -> str:
    """RSI shows enlistment like 'Jun 16, 2017' — reformat to 'Fri Jun 16 2017'; if the scrape
    shape ever changes, fall back to showing whatever we got."""
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%a %b %d %Y")
        except ValueError:
            continue
    return raw


def _build_embed(payload: dict, fallback_avatar: str) -> discord.Embed:
    default_rsi = "https://cdn.robertsspaceindustries.com/static/images/account/avatar_default_big.jpg"
    avatar = payload.get("victim_avatar") or ""
    if not avatar or avatar == default_rsi:
        avatar = fallback_avatar

    killer = "BlightVeil" if payload.get("anonymous") else payload["killer_name"]
    victim = payload["victim"]
    org    = payload.get("victim_org") or "Unknown"
    org_sid = payload.get("victim_org_sid") or ""
    org_link = f"https://robertsspaceindustries.com/orgs/{org_sid}" if org_sid else None

    try:
        ts = datetime.fromisoformat(payload["time"].replace("Z", "+00:00"))
    except Exception:
        ts = datetime.now(dt_tz.utc)

    incap = bool(payload.get("incap"))
    e = discord.Embed(
        title="BlightVeil Incap" if incap else "BlightVeil Kill",
        description=(
            f"{killer} incapacitated [{victim}](https://robertsspaceindustries.com/citizens/{victim})"
            if incap else
            f"{killer} eliminated [{victim}](https://robertsspaceindustries.com/citizens/{victim})"
        ),
        color=0xB8860B if incap else 0x690404,  # amber for incaps, blood red for kills
        timestamp=ts,
    )
    e.set_thumbnail(url=avatar)
    e.add_field(name="Time", value=discord.utils.format_dt(ts, style="R"), inline=False)
    _add_field(e, "Killer", killer)
    # Only show a ship when the fight actually involved one — "FPS" means on foot.
    ship = payload.get("killers_ship") or ""
    if ship and ship.upper() != "FPS":
        _add_field(e, "Killer's Ship", display_ship(cleanup_name(ship)))
    if payload.get("weapon"):
        _add_field(e, "Killer's Weapon", display_weapon(cleanup_name(payload["weapon"])))
    _add_field(e, "Victim's Name", f"[{victim}](https://robertsspaceindustries.com/citizens/{victim})")
    zone = payload.get("zone") or ""
    if zone and zone.upper() != "FPS":
        _add_field(e, "Victim Zone", cleanup_name(zone))
    _add_field(e, "Organization", f"[{org}]({org_link})" if org_link else (payload.get("victim_org") or ""))
    if payload.get("victim_enlisted"):
        _add_field(e, "Enlisted", _fmt_enlisted(payload["victim_enlisted"]))
    _add_field(e, "Location", payload.get("victim_location"))
    gm = payload.get("game_mode", "")
    if gm and gm != "SC_Default":
        _add_field(e, "Game Mode", display_game_mode(gm))
    e.set_footer(text="By Any Means, We Prosper!")
    return e


class KillTrackerCog(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        self.ApiKey = apps.get_model("killtracker", "ApiKey")
        self.GameModeChannel = apps.get_model("killtracker", "GameModeChannel")
        self._pubsub = None
        self._task = None
        self._listen.start()

    def cog_unload(self):
        self._listen.cancel()
        if self._pubsub:
            asyncio.create_task(self._pubsub.close())

    async def _get_channel_id(self, game_mode: str) -> int:
        @sync_to_async
        def _lookup():
            match = self.GameModeChannel.objects.filter(game_mode_key=game_mode).first()
            if match:
                return match.channel_id
            default = self.GameModeChannel.objects.filter(is_default=True).first()
            return default.channel_id if default else None

        cid = await _lookup()
        if cid:
            return cid
        from app.killtracker.preferences import KTDefaultChannelID
        return await aget_global_preference(KTDefaultChannelID.import_path)

    @tasks.loop(seconds=1.0, reconnect=True)
    async def _listen(self):
        if self._pubsub is None:
            redis_url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
            r = aioredis.from_url(redis_url, decode_responses=True)
            self._pubsub = r.pubsub()
            await self._pubsub.subscribe(PUBSUB_CHANNEL)
            log.info("killtracker cog: subscribed to %s", PUBSUB_CHANNEL)

        msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if not msg:
            return
        try:
            data = json.loads(msg["data"])
        except Exception:
            return
        if data.get("type") != "KillEvent":
            return

        try:
            from app.killtracker.preferences import KTFallbackAvatar
            fallback = await aget_global_preference(KTFallbackAvatar.import_path)
            embed = _build_embed(data, fallback)
            cid = await self._get_channel_id(data.get("game_mode", ""))
            ch = self.client.get_channel(int(cid)) if cid else None
            if ch is None and cid:
                ch = await self.client.fetch_channel(int(cid))
            if ch:
                await ch.send(embed=embed)
        except Exception:
            log.exception("killtracker cog: dispatch failed for %s", data)

    # ── Slash commands ────────────────────────────────────────────────────────

    kt_group = app_commands.Group(
        name="killtracker",
        description="BlightVeil KillTracker commands",
    )

    @kt_group.command(name="generate-key", description="Generate a new KillTracker API key")
    @app_commands.describe(label="Optional label to identify this key (e.g. 'Main PC')")
    async def generate_key(self, interaction: discord.Interaction, label: str = ""):
        await interaction.response.defer(ephemeral=True, thinking=True)

        du = await _user_for_discord_id(interaction.user.id)
        if not du or not du.user:
            await interaction.followup.send("❌ Your Discord account isn't linked to a website account yet.", ephemeral=True)
            return

        has_perm = await sync_to_async(du.user.has_perm)("killtracker.can_generate_key")
        if not has_perm:
            await interaction.followup.send("❌ You don't have permission to generate a KillTracker key.", ephemeral=True)
            return

        key = await self.ApiKey.objects.acreate(user=du.user, label=(label or "")[:64])
        await interaction.followup.send(
            f"✅ **KillTracker API key generated.**\n"
            f"```{key.key}```\n"
            f"Paste this into your KillTracker client. You can manage / revoke your keys on the website dashboard.",
            ephemeral=True,
        )

    @kt_group.command(name="my-keys", description="List your KillTracker API keys")
    async def my_keys(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        du = await _user_for_discord_id(interaction.user.id)
        if not du or not du.user:
            await interaction.followup.send("❌ Discord account not linked.", ephemeral=True)
            return

        lines = []
        async for k in self.ApiKey.objects.filter(user=du.user).order_by("-created_at"):
            status = "🔴 revoked" if k.revoked else "🟢 active"
            label = f" ({k.label})" if k.label else ""
            lines.append(f"{status}{label} — `{k.key[:10]}…` created {k.created_at:%Y-%m-%d}")

        text = "\n".join(lines) if lines else "_No keys yet — use `/killtracker generate-key`._"
        await interaction.followup.send(text, ephemeral=True)

    @kt_group.command(name="revoke-key", description="Revoke one of your KillTracker API keys")
    @app_commands.describe(key_prefix="First characters of the key to revoke (shown in /killtracker my-keys)")
    async def revoke_key(self, interaction: discord.Interaction, key_prefix: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        du = await _user_for_discord_id(interaction.user.id)
        if not du or not du.user:
            await interaction.followup.send("❌ Discord account not linked.", ephemeral=True)
            return

        key_obj = await self.ApiKey.objects.filter(
            user=du.user, key__startswith=key_prefix, revoked=False
        ).afirst()
        if not key_obj:
            await interaction.followup.send(
                "❌ No active key found matching that prefix. Use `/killtracker my-keys` to see your keys.",
                ephemeral=True,
            )
            return

        key_obj.revoked = True
        await key_obj.asave(update_fields=["revoked"])
        label = f" ({key_obj.label})" if key_obj.label else ""
        await interaction.followup.send(
            f"✅ Key `{key_obj.key[:10]}…`{label} has been revoked.", ephemeral=True
        )

    @revoke_key.autocomplete("key_prefix")
    async def revoke_key_autocomplete(self, interaction: discord.Interaction, current: str):
        du = await _user_for_discord_id(interaction.user.id)
        if not du or not du.user:
            return []
        choices = []
        async for k in self.ApiKey.objects.filter(user=du.user, revoked=False).order_by("-created_at")[:25]:
            label = f"{k.key[:10]}… {('(' + k.label + ')') if k.label else ''} — {k.created_at:%Y-%m-%d}".strip()
            choices.append(app_commands.Choice(name=label, value=k.key[:10]))
        if current:
            choices = [c for c in choices if current.lower() in c.name.lower()]
        return choices[:25]


    @app_commands.command(name="rsilookup", description="Look up an RSI citizen profile by handle")
    @app_commands.describe(handle="Star Citizen player handle (username)")
    async def rsilookup(self, interaction: discord.Interaction, handle: str):
        await interaction.response.defer(thinking=True)

        handle = handle.strip().lstrip("@")
        profile = await sync_to_async(fetch_rsi_profile)(handle)

        if not profile.get("exists"):
            await interaction.followup.send(
                f"❌ Could not find an RSI citizen with handle `{handle}`.",
            )
            return

        # Detect changes vs last known snapshot
        Snapshot = apps.get_model("killtracker", "RSIProfileSnapshot")
        snap = await Snapshot.objects.filter(handle__iexact=handle).afirst()
        changes: list[str] = []
        if snap:
            if profile.get("display_name") and profile["display_name"] != snap.display_name:
                changes.append(f"**Moniker:** `{snap.display_name or '—'}` → `{profile['display_name']}`")
            if profile.get("org_name") and profile["org_name"] != snap.org_name:
                changes.append(f"**Org:** `{snap.org_name or '—'}` → `{profile['org_name']}`")
            if profile.get("org_short") and profile["org_short"] != snap.org_short:
                changes.append(f"**SID:** `{snap.org_short or '—'}` → `{profile['org_short']}`")
            snap.display_name  = profile.get("display_name") or snap.display_name
            snap.org_name      = profile.get("org_name") or snap.org_name
            snap.org_short     = profile.get("org_short") or snap.org_short
            snap.avatar_url    = profile.get("avatar_url") or snap.avatar_url
            snap.enlisted_date = profile.get("enlisted_date") or snap.enlisted_date
            await snap.asave()
        else:
            await Snapshot.objects.acreate(
                handle=handle,
                display_name=profile.get("display_name", ""),
                org_name=profile.get("org_name", ""),
                org_short=profile.get("org_short", ""),
                avatar_url=profile.get("avatar_url", ""),
                enlisted_date=profile.get("enlisted_date", ""),
            )

        display = profile.get("display_name") or handle
        org = profile.get("org_name") or "No organization"
        sid = profile.get("org_short") or ""
        org_field = f"[{org}](https://robertsspaceindustries.com/orgs/{sid})" if sid else org

        e = discord.Embed(
            title=f"RSI Profile — {display}",
            url=f"https://robertsspaceindustries.com/citizens/{handle}",
            color=0xFF3B3B,
            timestamp=datetime.now(dt_tz.utc),
        )
        if profile.get("avatar_url"):
            e.set_thumbnail(url=profile["avatar_url"])

        e.add_field(name="Handle", value=f"`{handle}`", inline=True)
        e.add_field(name="Enlisted", value=profile.get("enlisted_date") or "—", inline=True)
        e.add_field(name="Location", value=profile.get("location") or "—", inline=True)
        e.add_field(name="Organization", value=org_field, inline=True)
        e.add_field(name="SID", value=f"`{sid}`" if sid else "—", inline=True)
        website = profile.get("website") or ""
        e.add_field(name="Website", value=website if website else "—", inline=True)

        bio = profile.get("bio") or ""
        if bio:
            e.add_field(name="Bio", value=bio[:1000], inline=False)

        if changes:
            e.add_field(name="⚠️ Changes Since Last Lookup", value="\n".join(changes), inline=False)

        e.set_footer(text="BlightVeil KillTracker — By Any Means, We Prosper!")
        await interaction.followup.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(KillTrackerCog(bot))
