"""
Management command: import_guild_members

Pre-populates DiscordUser + OrgPlayer for every current member of the Discord guild
using the Discord REST API. Run this BEFORE switching the bot to a new server so that
members already in the guild are in the database and don't get their roles stripped on
first sync.

Usage:
    python main.py import_guild_members --guild-id <GUILD_ID> --token <BOT_TOKEN>
    python main.py import_guild_members --guild-id <GUILD_ID> --token <BOT_TOKEN> --dry-run
"""
import time

import requests
from django.core.management.base import BaseCommand, CommandError

from app.discordauth.models import DiscordUser


class Command(BaseCommand):
    help = "Import all current Discord guild members into the backend DB"

    def add_arguments(self, parser):
        parser.add_argument("--guild-id", required=True, type=int, help="Discord guild (server) ID")
        parser.add_argument("--token", required=True, type=str, help="Discord bot token")
        parser.add_argument("--dry-run", action="store_true", help="Preview only — no DB writes")
        parser.add_argument("--limit", type=int, default=1000, help="Max members per API page (1–1000)")

    def handle(self, *args, **options):
        guild_id = options["guild_id"]
        token = options["token"]
        dry_run = options["dry_run"]
        page_limit = min(max(options["limit"], 1), 1000)

        headers = {"Authorization": f"Bot {token}"}
        base_url = f"https://discord.com/api/v10/guilds/{guild_id}/members"

        created = 0
        existing = 0
        errors = 0
        after = 0

        self.stdout.write(f"Fetching members for guild {guild_id} (dry_run={dry_run})…")

        while True:
            params = {"limit": page_limit, "after": after}
            resp = requests.get(base_url, headers=headers, params=params, timeout=30)

            if resp.status_code == 429:
                retry_after = float(resp.json().get("retry_after", 1))
                self.stdout.write(self.style.WARNING(f"Rate limited — sleeping {retry_after}s"))
                time.sleep(retry_after)
                continue

            if resp.status_code != 200:
                raise CommandError(f"Discord API error {resp.status_code}: {resp.text}")

            members = resp.json()
            if not members:
                break

            for m in members:
                user = m.get("user", {})
                if not user or user.get("bot"):
                    continue

                discord_uid = int(user["id"])
                username = user.get("username", "")
                discriminator = user.get("discriminator", "0")
                global_name = user.get("global_name") or None
                nick = m.get("nick") or None
                avatar = user.get("avatar") or None

                if dry_run:
                    if not DiscordUser.objects.filter(discorduid=discord_uid).exists():
                        self.stdout.write(f"  [DRY-RUN] Would create: {username}#{discriminator} ({discord_uid})")
                        created += 1
                    else:
                        existing += 1
                    continue

                try:
                    du, was_created = DiscordUser.ensure_with_user(
                        discorduid=discord_uid,
                        access_token=None,
                        refresh_token=None,
                        access_token_expires=None,
                    )
                    # Update fields that ensure_with_user may not set
                    changed = False
                    if du.username != username:
                        du.username = username
                        changed = True
                    if du.global_name != global_name:
                        du.global_name = global_name
                        changed = True
                    if du.main_guild_nick != nick:
                        du.main_guild_nick = nick
                        changed = True
                    if changed:
                        du.save(update_fields=["username", "global_name", "main_guild_nick"])

                    if was_created:
                        created += 1
                    else:
                        existing += 1
                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f"  Error for {discord_uid}: {exc}"))
                    errors += 1

            last_id = int(members[-1]["user"]["id"])
            after = last_id

            self.stdout.write(f"  Page done — last_id={last_id}, total so far: created={created} existing={existing} errors={errors}")

            if len(members) < page_limit:
                break

            # Respect rate limits — Discord allows ~5 req/s on this endpoint
            time.sleep(0.25)

        summary = f"\nDone. created={created}  existing={existing}  errors={errors}"
        if dry_run:
            summary += "  [DRY-RUN — no writes made]"
        self.stdout.write(self.style.SUCCESS(summary))
