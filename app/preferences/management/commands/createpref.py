from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = 'Create Preferences'

    def add_arguments(self, parser):
        parser.add_argument(
            "--dont-remove-orphans",
            action="store_true",
            help="Don't delete database entries that are no longer in the registry",
        )

    def handle(self, *args, **options):
        remove_orphans = not options["dont_remove_orphans"]

        if remove_orphans:
            self.stdout.write(self.style.SUCCESS("Creating Preferences and removing orphans."))
        else:
            self.stdout.write(self.style.SUCCESS("Creating Preferences, not touching orphans."))

        with transaction.atomic():  # prob want all or nothing
            from ...utils import all_managers
            for manager in all_managers:
                manager.populate_db(remove_orphans=remove_orphans)

        self.stdout.write(self.style.SUCCESS("Done Creating Preferences"))
