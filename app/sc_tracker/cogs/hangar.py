import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from redis import asyncio as aioredis
import discord
from discord.ext import commands
from django.conf import settings

from app.sc_tracker.preferences import sc_prefs

logger = logging.getLogger(__name__)

PUBSUB_HANGAR = "sc_tracker:hangar_events"


def _build_hangar_embed(state: dict, bot_user) -> discord.Embed:
    phase   = state.get("phase", "RED")
    lights  = state.get("active_lights", 0)
    total   = state.get("total_lights", 5)
    remaining = state.get("time_remaining", 0)

    color = discord.Color.green() if phase == "GREEN" else discord.Color.red()

    phase_details = {
        "RED": (
            "❌ **DO NOT INSERT COMPBOARDS** — Hangar will not open\n"
            "🔴 Lights turn green every 24 minutes"
        ),
        "GREEN": (
            "✅ **COMPBOARDS CAN BE INSERTED** — Hangar is open\n"
            "🟢 Lights turn off every 12 minutes"
        ),
    }

    if phase == "RED":
        light_icons = "".join("🟢" if i < lights else "🔴" for i in range(total))
    else:
        light_icons = "".join("🟢" if i < lights else "⚫" for i in range(total))

    light_status = f"{lights}/{total} lights {'green' if phase == 'RED' else 'remaining'}"

    next_ts   = int(datetime.now(timezone.utc).timestamp() + remaining)
    next_text = f"{'Closes' if phase == 'GREEN' else 'Opens'} <t:{next_ts}:R>"

    embed = discord.Embed(
        title="🏢 EXECUTIVE HANGAR TIMER",
        description=f"*{state.get('patch_info', '')}*",
        url="https://blightveil.org/sc/",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(
        text="Calculated from official cycle · BlightVeil Servitor",
        icon_url=bot_user.display_avatar.url if bot_user else None,
    )
    embed.add_field(name=f"📊 {'Hangar Open' if phase == 'GREEN' else 'Hangar Closed'}", value=phase_details[phase], inline=False)
    embed.add_field(name="💡 LIGHT STATUS", value=f"{light_icons}\n{light_status}", inline=False)
    embed.add_field(name="⏰ TIME REMAINING", value=f"<t:{next_ts}:R>", inline=False)
    embed.add_field(name="🔄 NEXT PHASE", value=next_text, inline=False)
    embed.add_field(
        name="🔗 LINKS",
        value="[Ops Dashboard](https://blightveil.org/sc/) · [Official Timer](https://exec.xyxyll.com/)",
        inline=False,
    )
    return embed


def _build_phase_embed(old_phase: str, new_phase: str) -> discord.Embed:
    if new_phase == "GREEN":
        return discord.Embed(
            title="🔄 HANGAR PHASE TRANSITION",
            description="🎉 **EXECUTIVE HANGAR IS NOW OPEN!** 🎉\n✅ **COMPBOARDS CAN NOW BE INSERTED!**\n🟢 Hangar is in GREEN phase",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
    return discord.Embed(
        title="🔄 HANGAR PHASE TRANSITION",
        description="🔴 **EXECUTIVE HANGAR IS NOW CLOSED**\n❌ **DO NOT INSERT COMPBOARDS**\n🔴 Hangar is in RED phase",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )


class HangarCog(commands.Cog, name="Hangar Timer"):

    def __init__(self, client: discord.Client):
        self.client = client
        self._redis_task: Optional[asyncio.Task] = None
        self._channel: Optional[discord.TextChannel] = None
        self._last_phase: Optional[str] = None

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("[HANGAR COG] on_ready — starting Redis listener.")
        from asgiref.sync import sync_to_async
        from app.sc_tracker.tasks.hangar import _calc_state

        # Resolve channel (sync_to_async required — sc_prefs hits the DB)
        channel_id = await sync_to_async(lambda: sc_prefs.hangar_channel_id)()
        if channel_id:
            self._channel = self.client.get_channel(channel_id)

        # Post/update initial embed immediately on startup
        if self._channel:
            state = await sync_to_async(_calc_state)()
            self._last_phase = state.get("phase")
            await self._update_embed(state)

        # Always cancel and replace — prevents duplicate listeners on reconnect
        if self._redis_task and not self._redis_task.done():
            self._redis_task.cancel()
            try:
                await self._redis_task
            except asyncio.CancelledError:
                pass
        self._redis_task = asyncio.create_task(self._listen_redis())

    async def cog_unload(self):
        if self._redis_task:
            self._redis_task.cancel()

    async def _listen_redis(self):
        redis_url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
        try:
            redis = aioredis.from_url(redis_url, decode_responses=True)
            pubsub = redis.pubsub()
            await pubsub.subscribe(PUBSUB_HANGAR)
            logger.info("[HANGAR COG] Subscribed to %s", PUBSUB_HANGAR)
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    await self._handle_event(event)
                except Exception as exc:
                    logger.error("[HANGAR COG] Event error: %s", exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[HANGAR COG] Listener crashed: %s — restarting in 30s.", exc)
            await asyncio.sleep(30)
            # on_ready may have already spawned a replacement while we slept; don't double up
            if self._redis_task is None or self._redis_task.done():
                self._redis_task = asyncio.create_task(self._listen_redis())

    async def _handle_event(self, event: dict):
        event_type = event.get("type")
        if event_type == "update_embed":
            state     = event.get("state", {})
            new_phase = state.get("phase")
            if self._last_phase and self._last_phase != new_phase:
                await self._send_phase_notification(self._last_phase, new_phase)
            self._last_phase = new_phase
            await self._update_embed(state)
        elif event_type == "website_sync_complete":
            applied = event.get("applied", [])
            if applied:
                logger.info("[HANGAR COG] Website sync applied updates: %s", applied)
            else:
                logger.info("[HANGAR COG] Website sync complete — no changes.")

    async def _update_embed(self, state: dict):
        if not self._channel:
            return

        from asgiref.sync import sync_to_async
        from app.sc_tracker.models import HangarStatusMessage

        embed = _build_hangar_embed(state, self.client.user)

        @sync_to_async
        def _get_msg_id():
            rec = HangarStatusMessage.objects.filter(id=1).first()
            return rec.message_id if rec else None

        msg_id = await _get_msg_id()

        if msg_id:
            for attempt in range(3):
                try:
                    existing = await self._channel.fetch_message(msg_id)
                    await existing.edit(embed=embed)
                    return
                except discord.NotFound:
                    break
                except discord.DiscordServerError:
                    if attempt < 2:
                        await asyncio.sleep(5 * (attempt + 1))
                    else:
                        logger.warning("[HANGAR COG] Discord 503 after 3 attempts, skipping embed update.")
                        return

        new_msg = await self._channel.send(embed=embed)

        @sync_to_async
        def _save(mid=new_msg.id):
            HangarStatusMessage.objects.update_or_create(id=1, defaults={"message_id": mid})

        await _save()

    async def _send_phase_notification(self, old_phase: str, new_phase: str):
        if not self._channel:
            return

        from asgiref.sync import sync_to_async
        from app.sc_tracker.models import HangarStatusMessage

        # Delete the previous phase notification so it doesn't accumulate
        @sync_to_async
        def _get_notif_id():
            rec = HangarStatusMessage.objects.filter(id=1).first()
            return rec.phase_notification_message_id if rec else None

        old_notif_id = await _get_notif_id()
        if old_notif_id:
            try:
                old_msg = await self._channel.fetch_message(old_notif_id)
                await old_msg.delete()
            except discord.NotFound:
                pass
            except Exception as exc:
                logger.warning("[HANGAR COG] Could not delete old phase notification: %s", exc)

        role_id = await sync_to_async(lambda: sc_prefs.hangar_phase_role_id)()
        content = f"<@&{role_id}>" if role_id else ""
        embed   = _build_phase_embed(old_phase, new_phase)
        try:
            msg = await self._channel.send(content=content, embed=embed)

            @sync_to_async
            def _save_notif_id(mid=msg.id):
                HangarStatusMessage.objects.update_or_create(
                    id=1, defaults={"phase_notification_message_id": mid}
                )

            await _save_notif_id()
        except Exception as exc:
            logger.error("[HANGAR COG] Phase notification failed: %s", exc)


async def setup(client: discord.Client):
    await client.add_cog(HangarCog(client))
    logger.info("[HANGAR COG] Loaded.")