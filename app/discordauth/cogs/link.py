"""
app/discordauth/cogs/link.py

Staff command for binding a Discord member to a pre-existing site OrgPlayer account:

  /link-account member:<@member> account:<username>
    Staff override: directly link any member to a site account.
    Requires Discord administrator permission + Django perm
    discordauth.can_link_discord_accounts.

The old self-service /link code flow was removed: linking is automatic at login via Discord
OAuth (DiscordAuthBackend.ensure_with_user). Pre-existing accounts (admin/password-created)
are bound by staff via this command or directly in Django admin. Removing the code-redemption
path also removes the only brute-forceable surface in the auth flow.
"""
import logging

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands
from django.contrib.auth import get_user_model

from app.main.util.discord_command_checks import requires_django_perm

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers (sync — called via sync_to_async)
# ---------------------------------------------------------------------------

def _staff_link(discord_uid: int, site_username: str):
    """Staff override: link by username.  Returns (discorduser, error_str_or_None)."""
    from app.discordauth.models import DiscordUser
    User = get_user_model()

    try:
        user = User.objects.get(username=site_username)
    except User.DoesNotExist:
        return None, f"No site account with username `{site_username}`."

    if hasattr(user, "discorduser"):
        if user.discorduser.discorduid == discord_uid:
            return user.discorduser, None  # already correct
        return None, "That site account is already linked to a different Discord."

    existing = DiscordUser.objects.filter(discorduid=discord_uid).first()
    if existing and existing.user_id and existing.user_id != user.pk:
        return None, "That Discord member is already linked to a different site account."

    if existing:
        existing.user = user
        existing.save(update_fields=["user"])
        return existing, None

    du = DiscordUser.objects.create(discorduid=discord_uid, user=user)
    return du, None


def _sync_roles(discorduid: int):
    """Fire role-sync Celery task (best-effort)."""
    from app.discordauth.models import DiscordUser
    try:
        du = DiscordUser.objects.get(discorduid=discorduid)
        du.task_sync_discord_roles_linked_groups()
    except Exception:
        log.exception("_sync_roles failed for discorduid=%s", discorduid)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AccountLinkCog(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------ #
    # /link-account member:<@member> account:<username>  — staff override
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="link-account",
        description="[Staff] Link a Discord member directly to a site account.",
    )
    @app_commands.describe(
        member="The Discord member to link.",
        account="The site username (OrgPlayer) to link them to.",
    )
    @app_commands.default_permissions(administrator=True)
    @requires_django_perm("discordauth.can_link_discord_accounts")
    async def cmd_link_account(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        account: str,
    ):
        await interaction.response.defer(ephemeral=True)

        du, error = await sync_to_async(_staff_link)(member.id, account.strip())
        if error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return

        await sync_to_async(_sync_roles)(member.id)

        await interaction.followup.send(
            f"✅ {member.mention} is now linked to site account **{account}**.",
            ephemeral=True,
        )
        self.logger.info(
            "AccountLinkCog: staff %s linked Discord %s → account %s",
            interaction.user.id, member.id, account,
        )


async def setup(client):
    await client.add_cog(AccountLinkCog(client))
