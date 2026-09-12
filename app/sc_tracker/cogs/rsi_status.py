import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from redis import asyncio as aioredis
import discord
from discord.ext import commands
from django.conf import settings

logger = logging.getLogger(__name__)

# Redis Pub/Sub channels published by Celery tasks
PUBSUB_RSI    = "sc_tracker:rsi_events"


from app.sc_tracker.preferences import sc_prefs


# ── Permission helper ─────────────────────────────────────────────────────────

async def _has_subscribe_permission(discord_user_id: int) -> bool:
    """
    Check Django Permission sc_tracker.can_subscribe_status for the given
    Discord user via OrgPlayer.

    Returns False if no OrgPlayer exists (graceful denial).
    """
    from asgiref.sync import sync_to_async

    @sync_to_async
    def _check():
        try:
            from app.discordauth.models import DiscordUser
            du = DiscordUser.objects.select_related("user").filter(discorduid=discord_user_id).first()
            if not du or not du.user:
                return False
            return du.user.has_perm("sc_tracker.can_subscribe_status")
        except Exception as exc:
            logger.error("[RSI COG] Permission check failed for %s: %s", discord_user_id, exc)
            return False

    return await _check()


# ── Embed Builders ────────────────────────────────────────────────────────────

STATUS_TRANS = {
    "operational": {"emoji": "🟢", "name": "Operational"},
    "degraded":    {"emoji": "🟣", "name": "Degraded"},
    "maintenance": {"emoji": "🛠️", "name": "Maintenance"},
    "partial":     {"emoji": "🟠", "name": "Partial Disruption"},
    "major":       {"emoji": "🔴", "name": "Major Disruption"},
    "issues":      {"emoji": "🟡", "name": "Issues"},
    "_other":      {"emoji": "❓", "name": "Unknown"},
}

SEV_COLORS = {
    "major":    discord.Color.red(),
    "degraded": discord.Color.purple(),
    "partial":  discord.Color.orange(),
    "resolved": discord.Color.green(),
}


def _build_status_embed(snapshot_data: dict, sub_count: int, bot_user, recent_issues: list = None) -> discord.Embed:
    """Build the main RSI status dashboard embed from a snapshot dict."""
    embed = discord.Embed(
        title="🚀 Star Citizen — RSI Status Dashboard",
        color=discord.Color.blue(),
        url="https://status.robertsspaceindustries.com/",
        timestamp=datetime.now(timezone.utc),
    )

    systems = snapshot_data.get("systems", [])

    # Service overview
    if systems:
        overview = "\n".join(
            f"{STATUS_TRANS.get(s.get('status','_other'), STATUS_TRANS['_other'])['emoji']} "
            f"**{s['name']}**: {STATUS_TRANS.get(s.get('status','_other'), STATUS_TRANS['_other'])['name']}"
            for s in systems
        )
        embed.add_field(name="🛠️ SERVICE STATUS", value=overview or "—", inline=False)

    # Active incidents — deduplicate by filename across systems
    seen_filenames: set = set()
    issues = []
    for s in systems:
        for i in s.get("unresolvedIssues", []):
            fname = i.get("filename", "") or i.get("title", "")
            if fname in seen_filenames:
                continue
            seen_filenames.add(fname)
            issues.append({**i, "system": s["name"]})

    if issues:
        for issue in issues[:3]:
            sev = issue.get("severity", "partial")
            sev_icon = {"major": "🔴", "degraded": "🟣", "partial": "🟠"}.get(sev, "⚠️")
            affected = ", ".join(issue.get("affected", [])) or issue.get("system", "Unknown")
            incident_url = _clean_permalink(issue.get("permalink", "")) or None
            incident_url = incident_url if incident_url and incident_url.strip("/") else None
            title_text = issue.get('title', 'Unknown Issue')[:60]
            field_name = f"{sev_icon} {title_text}"
            value = f"**Affected:** {affected}\n**Severity:** {sev.title()}"
            if incident_url:
                value += f"\n[↗ View on RSI Status Page]({incident_url})"
            embed.add_field(name=field_name, value=value, inline=False)
    else:
        embed.add_field(
            name="✅ ALL SYSTEMS OPERATIONAL",
            value="No active incidents — all services running normally.",
            inline=False,
        )

    # Build date
    if snapshot_data.get("buildDate"):
        try:
            build_dt = datetime.fromisoformat(
                f"{snapshot_data['buildDate']}T{snapshot_data.get('buildTime','00:00:00')}Z"
            )
            embed.description = f"Last RSI change: {discord.utils.format_dt(build_dt, style='R')}"
        except Exception:
            pass

    if recent_issues:
        lines = []
        for r in recent_issues[:5]:
            url = _clean_permalink(r.get("permalink", ""))
            resolved_at = r.get("rsi_resolved_at")
            when = discord.utils.format_dt(resolved_at, style="R") if resolved_at else "recently"
            title = r.get("title", "Unknown")[:50]
            lines.append(f"✅ [{title}]({url}) · {when}" if url.strip("/") else f"✅ {title} · {when}")
        embed.add_field(name="📋 RECENT ACTIVITY", value="\n".join(lines), inline=False)

    embed.add_field(
        name="🔔 SUBSCRIPTIONS",
        value=f"**{sub_count}** user(s) subscribed to DM alerts",
        inline=True,
    )
    embed.add_field(
        name="🔗 LINKS",
        value="[Ops Dashboard](https://blightveil.org/sc/) · [RSI Status Page](https://status.robertsspaceindustries.com/)",
        inline=True,
    )
    embed.set_footer(
        text="Updated by BlightVeil Servitor · Data: status.robertsspaceindustries.com",
        icon_url=bot_user.display_avatar.url if bot_user else None,
    )
    return embed


def _clean_permalink(raw: str) -> str:
    base = raw.replace("index.html", "").replace("index.xml", "").rstrip("/")
    return f"{base}/index.html" if base else ""


def _build_issue_embed(issue_data: dict, bot_user) -> discord.Embed:
    """Build an issue embed for the status channel or DM."""
    sev      = issue_data.get("severity", "partial")
    resolved = issue_data.get("resolved", False)

    if resolved:
        color, prefix, icon = discord.Color.green(), "RESOLVED", "✅"
    elif sev == "major":
        color, prefix, icon = discord.Color.red(), "MAJOR OUTAGE", "🔴"
    elif sev == "degraded":
        color, prefix, icon = discord.Color.purple(), "DEGRADED", "🟣"
    elif sev == "maintenance":
        color, prefix, icon = discord.Color.yellow(), "MAINTENANCE", "🛠️"
    else:
        color, prefix, icon = discord.Color.orange(), "ACTIVE", "⚠️"

    permalink = _clean_permalink(issue_data.get("permalink", ""))
    url = permalink if permalink.strip("/") else None

    embed = discord.Embed(
        title=f"{icon} {prefix}: {issue_data.get('title', 'Unknown Issue')}",
        url=url,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    affected     = issue_data.get("affected", [])
    status_text  = "✅ Resolved" if resolved else "🔍 Investigating"
    body_content = issue_data.get("markdown_content", "").strip()

    desc = (
        f"**Affected:** {', '.join(affected) if affected else 'Live Service'}\n"
        f"**Severity:** {sev.title()}\n"
        f"**Status:** {status_text}\n"
    )
    if url:
        desc += f"\n[↗ View on RSI Status Page]({url})\n"
    if body_content:
        # Truncate to fit Discord's 4096-char embed description limit
        body_truncated = body_content[:3800] + ("…" if len(body_content) > 3800 else "")
        desc += f"\n---\n{body_truncated}"

    embed.description = desc
    embed.set_footer(
        text="Issue resolved" if resolved else "Investigating — BlightVeil Servitor",
        icon_url=bot_user.display_avatar.url if bot_user else None,
    )
    return embed


def _build_subscription_buttons() -> discord.ui.View:
    """Returns the persistent view with Subscribe / Unsubscribe buttons."""

    class SubscriptionView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(
            label="🔔 Subscribe",
            style=discord.ButtonStyle.primary,
            custom_id="sc_status:subscribe",
        )
        async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
            await _handle_subscribe(interaction)

        @discord.ui.button(
            label="🔕 Unsubscribe",
            style=discord.ButtonStyle.secondary,
            custom_id="sc_status:unsubscribe",
        )
        async def unsubscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
            await _handle_unsubscribe(interaction)

    return SubscriptionView()


# ── Button Handlers ───────────────────────────────────────────────────────────

async def _handle_subscribe(interaction: discord.Interaction):
    """Subscribe button — checks Django permission before acting."""
    from asgiref.sync import sync_to_async
    from app.sc_tracker.models import StatusDMMessage, StatusSubscription

    await interaction.response.defer(ephemeral=True)

    # 1. Django permission gate
    has_perm = await _has_subscribe_permission(interaction.user.id)
    if not has_perm:
        await interaction.followup.send(
            "❌ You don't have permission to subscribe to RSI status alerts.",
            ephemeral=True,
        )
        return

    # 2. Already subscribed?
    @sync_to_async
    def _get_sub():
        return StatusSubscription.objects.filter(
            discord_user_id=interaction.user.id, is_active=True
        ).first()

    existing = await _get_sub()
    if existing:
        await interaction.followup.send(
            "✅ You're already subscribed to RSI status DM alerts!", ephemeral=True
        )
        return

    # 3. Test DM open
    try:
        dm_embed = discord.Embed(
            title="🔔 RSI Status Alerts — Subscribed",
            description=(
                "You'll receive DM alerts for:\n"
                "• New incidents / outages\n"
                "• Service status changes\n"
                "• Maintenance windows\n"
                "• Resolution updates\n\n"
                "To unsubscribe, click **🔕 Unsubscribe** in the status channel."
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        dm_msg = await interaction.user.send(embed=dm_embed)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I couldn't send you a DM — please open your DMs and try again.",
            ephemeral=True,
        )
        return

    # 4. Persist subscription + DM record
    @sync_to_async
    def _save_sub():
        sub, _ = StatusSubscription.objects.update_or_create(
            discord_user_id=interaction.user.id,
            defaults={"is_active": True},
        )
        StatusDMMessage.objects.create(
            discord_user_id=interaction.user.id,
            dm_message_id=dm_msg.id,
            message_type="confirmation",
        )
        return sub

    await _save_sub()

    # 5. Send any currently-active issues to the new subscriber
    await _send_active_issues_to_user(interaction.user)

    await interaction.followup.send(
        "✅ Subscribed! Check your DMs for a confirmation and any current active issues.",
        ephemeral=True,
    )
    logger.info("[RSI COG] %s (%d) subscribed.", interaction.user.name, interaction.user.id)


async def _handle_unsubscribe(interaction: discord.Interaction):
    """Unsubscribe button — checks Django permission, cleans up DMs."""
    from asgiref.sync import sync_to_async
    from app.sc_tracker.models import StatusDMMessage, StatusSubscription

    await interaction.response.defer(ephemeral=True)

    has_perm = await _has_subscribe_permission(interaction.user.id)
    if not has_perm:
        await interaction.followup.send(
            "❌ You don't have permission to manage RSI status subscriptions.",
            ephemeral=True,
        )
        return

    @sync_to_async
    def _get_and_deactivate():
        sub = StatusSubscription.objects.filter(
            discord_user_id=interaction.user.id, is_active=True
        ).first()
        if sub:
            sub.is_active = False
            sub.save(update_fields=["is_active"])
        msgs = list(
            StatusDMMessage.objects.filter(discord_user_id=interaction.user.id)
        )
        return sub, msgs

    sub, msgs = await _get_and_deactivate()

    if not sub:
        await interaction.followup.send("❌ You're not currently subscribed.", ephemeral=True)
        return

    # Delete DM messages
    deleted = 0
    for record in msgs:
        try:
            dm_channel = interaction.user.dm_channel or await interaction.user.create_dm()
            msg = await dm_channel.fetch_message(record.dm_message_id)
            await msg.delete()
            deleted += 1
        except Exception:
            pass  # already gone

    @sync_to_async
    def _purge_db():
        StatusDMMessage.objects.filter(discord_user_id=interaction.user.id).delete()

    await _purge_db()

    await interaction.followup.send(
        f"✅ Unsubscribed. {deleted} DM message(s) cleaned up.",
        ephemeral=True,
    )
    logger.info("[RSI COG] %s (%d) unsubscribed.", interaction.user.name, interaction.user.id)


async def _send_active_issues_to_user(user: discord.User):
    """Send currently-active RSI issues to a newly-subscribed user."""
    from asgiref.sync import sync_to_async
    from app.sc_tracker.models import RSIIssue, StatusDMMessage

    @sync_to_async
    def _get_issues():
        return list(RSIIssue.objects.filter(resolved=False).values(
            "filename", "title", "affected", "severity", "permalink", "resolved"
        ))

    issues = await _get_issues()

    dm_channel = user.dm_channel or await user.create_dm()
    for issue_data in issues:
        try:
            embed = _build_issue_embed(issue_data, None)
            dm_msg = await dm_channel.send(embed=embed)

            @sync_to_async
            def _record(mid=dm_msg.id, fname=issue_data["filename"], title=issue_data["title"]):
                StatusDMMessage.objects.create(
                    discord_user_id=user.id,
                    dm_message_id=mid,
                    incident_guid=fname,
                    incident_title=title,
                    message_type="update",
                )

            await _record()
        except Exception as exc:
            logger.warning("[RSI COG] Failed to send active issue to %s: %s", user.name, exc)


# ── Cog ───────────────────────────────────────────────────────────────────────

class RSIStatusCog(commands.Cog, name="RSI Status"):
    """
    Manages the RSI status embed and subscriber DMs.
    Driven by Redis Pub/Sub events published by Celery tasks.
    """

    def __init__(self, client: discord.Client):
        self.client = client
        self._redis_task: Optional[asyncio.Task] = None
        self._status_channel: Optional[discord.TextChannel] = None
        self._updates_channel: Optional[discord.TextChannel] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("[RSI COG] on_ready — registering persistent views and starting Redis listener.")
        from asgiref.sync import sync_to_async

        # Register persistent button view so it works across restarts
        self.client.add_view(_build_subscription_buttons())

        # Resolve channels (sync_to_async required — sc_prefs hits the DB)
        channel_id, updates_channel_id = await sync_to_async(
            lambda: (sc_prefs.status_channel_id, sc_prefs.updates_channel_id)
        )()
        if channel_id:
            self._status_channel = self.client.get_channel(channel_id)
        if updates_channel_id:
            self._updates_channel = self.client.get_channel(updates_channel_id)

        # Initial embed post / update
        await self._refresh_status_embed()
        await self._post_active_issues_on_startup()

        # Start Redis listener
        if self._redis_task is None or self._redis_task.done():
            self._redis_task = asyncio.create_task(self._listen_redis())

    async def cog_unload(self):
        if self._redis_task:
            self._redis_task.cancel()

    # ── Redis Pub/Sub listener ────────────────────────────────────────────────

    async def _listen_redis(self):
        """
        Subscribe to sc_tracker:rsi_events and react to Celery-published events.
        Uses async Redis (aioredis) — never asyncio.run_coroutine_threadsafe().
        """
        redis_url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
        try:
            redis = aioredis.from_url(redis_url, decode_responses=True)
            pubsub = redis.pubsub()
            await pubsub.subscribe(PUBSUB_RSI)
            logger.info("[RSI COG] Subscribed to Redis channel: %s", PUBSUB_RSI)

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    events = json.loads(message["data"])
                    if isinstance(events, dict):
                        events = [events]
                    for event in events:
                        await self._handle_event(event)
                except Exception as exc:
                    logger.error("[RSI COG] Error handling Redis event: %s", exc)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[RSI COG] Redis listener crashed: %s — restarting in 30s.", exc)
            await asyncio.sleep(30)
            self._redis_task = asyncio.create_task(self._listen_redis())

    async def _handle_event(self, event: dict):
        event_type = event.get("type")

        if event_type in ("new_issue", "updated_issue", "resolved_issue"):
            await self._refresh_status_embed()
            await self._post_issue_update(event)

        elif event_type == "send_dm":
            await self._deliver_dm(event)

        elif event_type == "delete_dms":
            await self._delete_dms(event.get("messages", []))

        elif event_type == "constants_updated":
            logger.info("[RSI COG] Hangar constants updated via event: %s", event.get("fields"))

    # ── Embed Management ──────────────────────────────────────────────────────

    async def _update_channel_name(self, snapshot_data: dict):
        """Update the status channel name with colored circles for each system."""
        if not self._status_channel:
            return

        _STATUS_CIRCLES = {
            "operational": "🟢",
            "maintenance": "🟡",
            "degraded":    "🟠",
            "major":       "🔴",
        }
        systems = snapshot_data.get("systems", [])
        circles = "".join(
            _STATUS_CIRCLES.get(s.get("status", "").lower(), "⚪")
            for s in systems
        )
        new_name = f"sc-status-{circles}" if circles else "sc-status"

        if self._status_channel.name != new_name:
            try:
                await self._status_channel.edit(name=new_name)
            except Exception as exc:
                logger.warning("[RSI COG] Failed to update channel name: %s", exc)

    async def _refresh_status_embed(self):
        """Read the DB snapshot and update the status channel embed."""
        from asgiref.sync import sync_to_async
        from app.sc_tracker.models import RSIStatusSnapshot, StatusChannelMessage, StatusSubscription

        if not self._status_channel:
            return

        @sync_to_async
        def _load():
            from app.sc_tracker.models import RSIIssue
            snap      = RSIStatusSnapshot.objects.filter(id=1).first()
            sub_count = StatusSubscription.objects.filter(is_active=True).count()
            msg_rec   = StatusChannelMessage.objects.filter(channel_id=self._status_channel.id).first()
            recent    = list(
                RSIIssue.objects.filter(resolved=True)
                .order_by("-rsi_resolved_at", "-updated_at")
                .values("title", "permalink", "rsi_resolved_at")[:5]
            )
            return snap, sub_count, msg_rec, recent

        snap, sub_count, msg_rec, recent = await _load()
        if not snap:
            return

        await self._update_channel_name(snap.raw_json)
        embed = _build_status_embed(snap.raw_json, sub_count, self.client.user, recent_issues=recent)
        view  = _build_subscription_buttons()

        if msg_rec:
            try:
                existing = await self._status_channel.fetch_message(msg_rec.message_id)
                await existing.edit(embed=embed, view=view)
                return
            except discord.NotFound:
                pass

        new_msg = await self._status_channel.send(embed=embed, view=view)

        @sync_to_async
        def _save(mid=new_msg.id):
            StatusChannelMessage.objects.update_or_create(
                channel_id=self._status_channel.id,
                defaults={"message_id": mid},
            )

        await _save()

    async def _post_active_issues_on_startup(self):
        """On startup, post any active issues that haven't been posted yet (discord_id is null)."""
        if not self._updates_channel:
            return

        from asgiref.sync import sync_to_async

        @sync_to_async
        def _get_active():
            from app.sc_tracker.models import RSIIssue
            return list(RSIIssue.objects.filter(resolved=False).values(
                "id", "filename", "title", "affected", "severity", "permalink",
                "resolved", "discord_id", "markdown_content"
            ))

        issues = await _get_active()
        for issue_data in issues:
            try:
                embed = _build_issue_embed(issue_data, self.client.user)
                if not issue_data.get("discord_id"):
                    msg = await self._updates_channel.send(embed=embed)

                    @sync_to_async
                    def _save_id(mid=msg.id, iid=issue_data["id"]):
                        from app.sc_tracker.models import RSIIssue
                        RSIIssue.objects.filter(id=iid).update(discord_id=mid)

                    await _save_id()
                else:
                    # Re-edit on startup to pick up any content changes (e.g. body populated after restart)
                    try:
                        existing = await self._updates_channel.fetch_message(issue_data["discord_id"])
                        await existing.edit(embed=embed)
                    except discord.NotFound:
                        msg = await self._updates_channel.send(embed=embed)

                        @sync_to_async
                        def _update_id(mid=msg.id, iid=issue_data["id"]):
                            from app.sc_tracker.models import RSIIssue
                            RSIIssue.objects.filter(id=iid).update(discord_id=mid)

                        await _update_id()
            except Exception as exc:
                logger.warning("[RSI COG] Failed to post active issue on startup: %s", exc)

    async def _post_issue_update(self, event: dict):
        """Post an issue embed to the updates channel on new/updated/resolved events."""
        if not self._updates_channel:
            return

        filename = event.get("filename")
        if not filename:
            return

        from asgiref.sync import sync_to_async

        @sync_to_async
        def _get_issue():
            from app.sc_tracker.models import RSIIssue
            return RSIIssue.objects.filter(filename=filename).values(
                "id", "filename", "title", "affected", "severity", "permalink",
                "resolved", "discord_id", "markdown_content"
            ).first()

        issue_data = await _get_issue()
        if not issue_data:
            return

        event_type = event.get("type")
        try:
            embed = _build_issue_embed(issue_data, self.client.user)
            # For new issues, post fresh and record the message ID
            if event_type == "new_issue" or not issue_data.get("discord_id"):
                msg = await self._updates_channel.send(embed=embed)

                @sync_to_async
                def _save_id(mid=msg.id, iid=issue_data["id"]):
                    from app.sc_tracker.models import RSIIssue
                    RSIIssue.objects.filter(id=iid).update(discord_id=mid)

                await _save_id()
            else:
                # For updates/resolved, edit the existing message then post a follow-up
                try:
                    existing = await self._updates_channel.fetch_message(issue_data["discord_id"])
                    await existing.edit(embed=embed)
                except discord.NotFound:
                    msg = await self._updates_channel.send(embed=embed)

                    @sync_to_async
                    def _update_id(mid=msg.id, iid=issue_data["id"]):
                        from app.sc_tracker.models import RSIIssue
                        RSIIssue.objects.filter(id=iid).update(discord_id=mid)

                    await _update_id()
        except Exception as exc:
            logger.warning("[RSI COG] Failed to post issue update to updates channel: %s", exc)

    # ── DM Delivery ───────────────────────────────────────────────────────────

    async def _deliver_dm(self, event: dict):
        """Send a status DM to a subscriber as instructed by Celery."""
        from asgiref.sync import sync_to_async
        from app.sc_tracker.models import StatusDMMessage

        user_id   = event.get("discord_user_id")
        evt_data  = event.get("event", {})
        if not user_id:
            return

        try:
            user = await self.client.fetch_user(user_id)
        except Exception:
            return

        # Load issue details from DB
        @sync_to_async
        def _get_issue(fname=evt_data.get("filename")):
            from app.sc_tracker.models import RSIIssue
            return RSIIssue.objects.filter(filename=fname).values(
                "filename", "title", "affected", "severity", "permalink", "resolved", "markdown_content"
            ).first()

        issue_data = await _get_issue()
        if not issue_data:
            return

        try:
            dm = user.dm_channel or await user.create_dm()
            embed = _build_issue_embed(issue_data, self.client.user)
            dm_msg = await dm.send(embed=embed)

            @sync_to_async
            def _record(mid=dm_msg.id):
                StatusDMMessage.objects.create(
                    discord_user_id=user_id,
                    dm_message_id=mid,
                    incident_guid=issue_data["filename"],
                    incident_title=issue_data["title"],
                    message_type="update",
                )

            await _record()

        except discord.Forbidden:
            # DMs closed — deactivate subscription
            from asgiref.sync import sync_to_async
            from app.sc_tracker.models import StatusSubscription

            @sync_to_async
            def _deactivate():
                StatusSubscription.objects.filter(discord_user_id=user_id).update(is_active=False)

            await _deactivate()
            logger.info("[RSI COG] Deactivated sub for %d — DMs closed.", user_id)

        except Exception as exc:
            logger.warning("[RSI COG] Failed to deliver DM to %d: %s", user_id, exc)

    async def _delete_dms(self, messages: list[dict]):
        """Delete Discord DM messages as instructed by the cleanup Celery task."""
        for record in messages:
            try:
                user = await self.client.fetch_user(record["discord_user_id"])
                dm = user.dm_channel or await user.create_dm()
                msg = await dm.fetch_message(record["dm_message_id"])
                await msg.delete()
            except Exception:
                pass  # already gone — silent skip


async def setup(client: discord.Client):
    await client.add_cog(RSIStatusCog(client))
    logger.info("[RSI COG] Loaded.")