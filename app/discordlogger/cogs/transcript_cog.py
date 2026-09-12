"""
app/discordlogger/cogs/transcript_cog.py

/transcript command — pull message logs for a channel from the DB and
export as a plain-text file.  Requires discordlogger.view_transcripts perm.
"""
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.main.util.discord_command_checks import requires_django_perm

log = logging.getLogger(__name__)

_PERM = "discordlogger.view_transcripts"
_MAX_MESSAGES = 500


class TranscriptCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="transcript",
        description="Export message log for a channel as a text file (staff only)",
    )
    @app_commands.describe(
        channel="Channel to pull the transcript from",
        limit="Number of messages to include (max 500, default 200)",
    )
    @requires_django_perm(_PERM)
    async def transcript(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        limit: int = 200,
    ):
        from app.discordlogger.models import LoggedDiscordMessage

        await interaction.response.defer(ephemeral=True, thinking=True)

        limit = max(1, min(limit, _MAX_MESSAGES))

        msgs = [
            m async for m in
            LoggedDiscordMessage.objects
            .filter(channel_id=channel.id, event=LoggedDiscordMessage.Event.NEW)
            .select_related("author")
            .order_by("-created_at")[:limit]
        ]
        msgs.reverse()  # oldest first

        if not msgs:
            await interaction.followup.send(
                f"No logged messages found for {channel.mention}.", ephemeral=True
            )
            return

        lines = [
            f"# Transcript — #{channel.name} (last {len(msgs)} messages)",
            f"# Exported by {interaction.user} at {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "",
        ]
        for m in msgs:
            author = str(m.author.discorduid) if m.author_id else m.author_name or "unknown"
            ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content = (m.content or "").replace("\n", "\n    ")
            lines.append(f"[{ts}] <{author}> {content}")
            if m.attachments:
                for att in m.attachments:
                    lines.append(f"    [attachment] {att}")

        text = "\n".join(lines)
        buf  = io.BytesIO(text.encode("utf-8"))
        filename = f"transcript_{channel.name}_{discord.utils.utcnow().strftime('%Y%m%d_%H%M')}.txt"

        await interaction.followup.send(
            f"📋 Transcript for {channel.mention} — {len(msgs)} messages",
            file=discord.File(buf, filename=filename),
            ephemeral=True,
        )

        try:
            from app.discordlogger.models import TranscriptExportLog
            from asgiref.sync import sync_to_async
            await sync_to_async(TranscriptExportLog.objects.create)(
                exporter_discord_id=interaction.user.id,
                channel_id=channel.id,
                channel_name=channel.name,
                message_count=len(msgs),
            )
        except Exception:
            log.exception("Failed to write TranscriptExportLog")

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        from app.main.util.discord_command_checks import MissingDjangoPermission, AccountNotLinked
        if isinstance(error, MissingDjangoPermission):
            msg = f"🛡️ **Permission denied:** you need `{error.missing_perm}`."
        elif isinstance(error, AccountNotLinked):
            msg = "🎮 Your Discord account isn't linked to an org profile yet."
        else:
            msg = "❌ An unexpected error occurred."
            log.exception("TranscriptCog error: %s", error)

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TranscriptCog(bot))
