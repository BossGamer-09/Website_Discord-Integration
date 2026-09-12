"""
app/merits/cogs/merits_cog.py

Listens for MeritRequest Redis events (published by app.merits.signals) and:
  - Posts new submission embeds to the staff review channel
  - DMs requesters when their request is fulfilled or denied

Actions handled:
  MERIT_REQUEST_SUBMITTED  → post to MeritStaffChannelID
  MERIT_REQUEST_FULFILLED  → DM requester
  MERIT_REQUEST_DENIED     → DM requester
"""
import json
import logging

import discord
from discord.ext import commands, tasks

from app.merits.signals import PUBSUB_CHANNEL
from app.preferences.utils import aget_global_preference
from app.merits.preferences import MeritNotificationsEnabled, MeritStaffChannelID

log = logging.getLogger(__name__)

_KIND_EMOJI = {"MONEY": "💰", "MERIT": "🏅"}
_KIND_LABEL = {"MONEY": "aUEC Payout", "MERIT": "Merit Award"}


def _submitted_embed(data: dict) -> discord.Embed:
    kind = data.get("kind", "")
    embed = discord.Embed(
        title=f"{_KIND_EMOJI.get(kind, '📋')} New {_KIND_LABEL.get(kind, 'Request')}: {data.get('title') or 'Untitled'}",
        color=0xFFC107,
    )
    embed.add_field(name="Kind",   value=_KIND_LABEL.get(kind, kind), inline=True)
    amount = data.get("amount")
    if amount:
        label = "aUEC" if kind == "MONEY" else "pts"
        embed.add_field(name="Amount", value=f"{amount:,} {label}", inline=True)
    if data.get("detail_url"):
        embed.add_field(name="Review", value=data["detail_url"], inline=False)
    embed.set_footer(text=f"Request #{data.get('request_pk')}")
    return embed


def _resolution_embed(data: dict, fulfilled: bool) -> discord.Embed:
    kind  = data.get("kind", "")
    color = 0x22C55E if fulfilled else 0xEF4444
    title = (
        f"✅ {_KIND_LABEL.get(kind, 'Request')} Fulfilled"
        if fulfilled
        else f"❌ {_KIND_LABEL.get(kind, 'Request')} Denied"
    )
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Request", value=data.get("title") or f"#{data.get('request_pk')}", inline=False)
    amount = data.get("amount")
    if amount and fulfilled:
        label = "aUEC" if kind == "MONEY" else "pts"
        embed.add_field(name="Amount", value=f"{amount:,} {label}", inline=True)
    note = data.get("reviewer_note", "").strip()
    if note:
        embed.add_field(name="Staff Note", value=note, inline=False)
    return embed


class MeritsCog(commands.Cog):
    def __init__(self, client):
        self.client      = client
        self.redis_client = None
        self.pubsub      = None

    async def cog_load(self):
        from django.conf import settings
        import redis.asyncio as redis
        pool              = redis.ConnectionPool.from_url(settings.CACHES["default"]["LOCATION"])
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
            if action == "MERIT_REQUEST_SUBMITTED":
                await self._on_submitted(data)
            elif action == "MERIT_REQUEST_FULFILLED":
                await self._on_resolved(data, fulfilled=True)
            elif action == "MERIT_REQUEST_DENIED":
                await self._on_resolved(data, fulfilled=False)
            else:
                log.debug("MeritsCog: unhandled action %s", action)
        except Exception:
            log.exception("MeritsCog listener_loop error")

    @listener_loop.before_loop
    async def _before_listener(self):
        await self.client.wait_until_ready()

    async def _on_submitted(self, data: dict):
        chan_id = int(await aget_global_preference(MeritStaffChannelID.import_path) or 0)
        if not chan_id:
            log.warning("MeritsCog: MeritStaffChannelID not configured")
            return
        channel = self.client.get_channel(chan_id)
        if not channel:
            log.warning("MeritsCog: staff channel %s not in cache", chan_id)
            return
        await channel.send(embed=_submitted_embed(data))

    async def _on_resolved(self, data: dict, fulfilled: bool):
        notifications_on = await aget_global_preference(MeritNotificationsEnabled.import_path)
        if not notifications_on:
            return

        discord_id = data.get("discord_id")
        if not discord_id:
            return

        guild_id = await self.client.aget_main_guild_id()
        guild    = self.client.get_guild(guild_id)
        member   = guild.get_member(int(discord_id)) if guild else None

        if not member:
            try:
                member = await self.client.fetch_user(int(discord_id))
            except discord.NotFound:
                return

        try:
            await member.send(embed=_resolution_embed(data, fulfilled))
        except discord.Forbidden:
            log.warning("MeritsCog: cannot DM user %s", discord_id)


async def setup(client):
    await client.add_cog(MeritsCog(client))
