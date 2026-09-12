"""Publish a KillTracker desktop client release from files already on the server.

Use this instead of the admin's file-upload form for the exe: a ~27 MB HTTP upload through
Cloudflare hits the 100 s proxy timeout (Error 524). SFTP the built exe(s) into the container
first, then point this command at them — it copies them into media storage, computes the
SHA-256 checksums, and (optionally) marks the release active. No HTTP upload involved.

Example:
    python manage.py kt_publish_release \
        --version 1.7 \
        --exe "/home/container/uploads/BlightVeil KillTracker.exe" \
        --exe-nosound "/home/container/uploads/BlightVeil KillTracker (no sounds).exe" \
        --notes "Patch 4.8 release" \
        --activate
"""
import os

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Publish a ClientRelease from exe files already present on the server (no HTTP upload)."

    def add_arguments(self, parser):
        parser.add_argument("--version", required=True, help="Release version, e.g. 1.7 (must be > clients' version to trigger an update).")
        parser.add_argument("--exe", required=True, help="Path to the with-sounds 'BlightVeil KillTracker.exe'.")
        parser.add_argument("--exe-nosound", default="", help="Path to the no-sounds exe (optional; falls back to the with-sounds file).")
        parser.add_argument("--notes", default="", help="Release notes shown on the dashboard.")
        parser.add_argument("--activate", action="store_true", help="Mark this release active (clients will update to it).")

    def handle(self, *args, **opts):
        from app.killtracker.models import ClientRelease

        exe_path = opts["exe"]
        exe_ns_path = opts["exe_nosound"]
        if not os.path.isfile(exe_path):
            raise CommandError(f"--exe not found: {exe_path}")
        if exe_ns_path and not os.path.isfile(exe_ns_path):
            raise CommandError(f"--exe-nosound not found: {exe_ns_path}")

        release, created = ClientRelease.objects.get_or_create(version=opts["version"])
        release.notes = opts["notes"] or release.notes
        if opts["activate"]:
            release.is_active = True

        # Save into media storage. save=False here so both files attach before the single
        # model.save() below, which computes the checksums from the stored files.
        with open(exe_path, "rb") as fh:
            release.file.save("BlightVeil KillTracker.exe", File(fh), save=False)
        if exe_ns_path:
            with open(exe_ns_path, "rb") as fh:
                release.file_nosound.save("BlightVeil KillTracker (no sounds).exe", File(fh), save=False)

        release.save()  # persists + computes sha256 / sha256_nosound

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} release {release.version} "
            f"({'ACTIVE' if release.is_active else 'inactive'})."
        ))
        self.stdout.write(f"  with-sounds sha256: {release.sha256}")
        if release.file_nosound:
            self.stdout.write(f"  no-sounds   sha256: {release.sha256_nosound}")
        if not opts["activate"]:
            self.stdout.write(self.style.WARNING("  Not active — re-run with --activate (or tick 'Is active' in admin) to roll it out."))
