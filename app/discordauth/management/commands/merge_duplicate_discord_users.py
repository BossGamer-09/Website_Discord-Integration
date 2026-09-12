"""
Management command: merge_duplicate_discord_users

Finds OrgPlayer duplicates created by the old username-prefix bug
(discord_<uid>_1, discord_<uid>_2, etc.) and merges them into the
canonical user that is linked to a DiscordUser record.

Safe to run multiple times (idempotent).
"""
import re
import logging
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger(__name__)

# Matches: discord_<uid>, discord_<uid>_1, discorduid.<uid>, discorduid.<uid>_1
_UID_PATTERN = re.compile(r'^(?:discord_|discorduid\.)(\d+)(?:_\d+)?$')


def _extract_uid(username):
    m = _UID_PATTERN.match(username)
    return int(m.group(1)) if m else None


def merge_duplicates_for_uid(uid, dry_run=False, log=None):
    """
    Merge all OrgPlayer duplicates for a single Discord UID into one canonical user.

    Canonical priority:
      1. The OrgPlayer that has a DiscordUser linked (user.discorduser exists)
      2. Oldest by date_joined among the rest

    Returns (canonical_pk, merged_count) or (None, 0) if nothing to do.
    """
    from app.discordauth.models import DiscordUser

    if log is None:
        log = logger.info

    User = get_user_model()

    candidates = list(
        User.objects.filter(
            username__regex=r'^(discord_|discorduid\.)' + str(uid) + r'(_\d+)?$'
        ).order_by('date_joined')
    )

    if len(candidates) <= 1:
        return (candidates[0].pk if candidates else None, 0)

    # Pick the one linked to a DiscordUser as canonical; fall back to oldest
    canonical = next(
        (u for u in candidates if hasattr(u, 'discorduser') and u.discorduser is not None),
        None,
    )
    if canonical is None:
        # None linked yet — pick oldest; _attach_user will link it after
        canonical = candidates[0]

    duplicates = [u for u in candidates if u.pk != canonical.pk]
    log("merge uid=%s canonical=%s duplicates=%s dry_run=%s", uid, canonical.pk, [u.pk for u in duplicates], dry_run)

    if dry_run:
        return (canonical.pk, len(duplicates))

    with transaction.atomic():
        for dup in duplicates:
            _repoint_fks(dup, canonical, log)
            dup.delete()

    return (canonical.pk, len(duplicates))


def _repoint_fks(source, target, log):
    """Re-point every FK/M2M from source OrgPlayer to target OrgPlayer."""
    from django.contrib.auth.models import Permission
    from guardian.models import UserObjectPermission

    # Groups
    target.groups.add(*source.groups.all())
    # User permissions
    target.user_permissions.add(*source.user_permissions.all())
    # guardian per-object perms
    UserObjectPermission.objects.filter(user=source).update(user=target)

    # All FK/O2O fields pointing at OrgPlayer via related managers
    for rel in source._meta.related_objects:
        related_model = rel.related_model
        field_name = rel.field.name

        if rel.one_to_one:
            # O2O: only move if target doesn't already have one
            try:
                existing = getattr(source, rel.get_accessor_name(), None)
            except Exception:
                existing = None
            if existing is None:
                continue
            try:
                target_existing = related_model.objects.filter(**{field_name: target}).exists()
            except Exception:
                continue
            if not target_existing:
                try:
                    related_model.objects.filter(**{field_name: source}).update(**{field_name: target})
                    log("  repoint O2O %s.%s", related_model.__name__, field_name)
                except Exception:
                    logger.exception("  failed repoint O2O %s.%s", related_model.__name__, field_name)
        elif rel.one_to_many:
            try:
                count = related_model.objects.filter(**{field_name: source}).update(**{field_name: target})
                if count:
                    log("  repoint FK %s.%s count=%s", related_model.__name__, field_name, count)
            except Exception:
                logger.exception("  failed repoint FK %s.%s", related_model.__name__, field_name)
        elif rel.many_to_many:
            try:
                objs = list(related_model.objects.filter(**{field_name: source}))
                if objs:
                    accessor = getattr(target, rel.get_accessor_name())
                    accessor.add(*objs)
                    log("  repoint M2M %s.%s count=%s", related_model.__name__, field_name, len(objs))
            except Exception:
                logger.exception("  failed repoint M2M %s.%s", related_model.__name__, field_name)


def merge_all_duplicates(dry_run=False, log=None):
    """Scan all OrgPlayers for duplicate UIDs and merge them."""
    User = get_user_model()
    if log is None:
        log = logger.info

    seen_uids = {}
    for username in User.objects.values_list('username', flat=True):
        uid = _extract_uid(username)
        if uid is not None:
            seen_uids.setdefault(uid, 0)
            seen_uids[uid] += 1

    duplicate_uids = [uid for uid, count in seen_uids.items() if count > 1]
    log("merge_all_duplicates: found %d UIDs with duplicates", len(duplicate_uids))

    total_merged = 0
    for uid in duplicate_uids:
        _, count = merge_duplicates_for_uid(uid, dry_run=dry_run, log=log)
        total_merged += count

    log("merge_all_duplicates: merged %d duplicate OrgPlayers total", total_merged)
    return total_merged


class Command(BaseCommand):
    help = "Merge duplicate OrgPlayer records caused by the discord_<uid>_N username bug."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report without making changes")
        parser.add_argument('--uid', type=int, help="Only process a specific Discord UID")

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        uid = options.get('uid')

        def log(msg, *a):
            self.stdout.write(msg % a if a else msg)

        if uid:
            canonical_pk, count = merge_duplicates_for_uid(uid, dry_run=dry_run, log=log)
            self.stdout.write(self.style.SUCCESS(
                f"UID {uid}: canonical={canonical_pk}, merged {count} duplicate(s)"
            ))
        else:
            total = merge_all_duplicates(dry_run=dry_run, log=log)
            suffix = " (dry run)" if dry_run else ""
            self.stdout.write(self.style.SUCCESS(f"Done{suffix}: merged {total} duplicate OrgPlayer(s)"))
