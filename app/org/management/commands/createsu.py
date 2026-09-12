from django.contrib.auth.management.commands.createsuperuser import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings


class Command(BaseCommand):
    help = 'Create initial Super Users'

    def handle(self, *args, **options):
        user_model = get_user_model()
        for user in getattr(settings, "SUPERUSERS", []):
            username, email, password = user
            if not user_model.objects.filter(username=username).exists():
                self.stdout.write(self.style.SUCCESS('Creating Super User account for %s (%s).' % (username, email)))
                suser = user_model.objects.create_superuser(email=email, username=username, password=password)
                suser.is_active = True
                suser.save()
            else:
                self.stdout.write(self.style.SUCCESS('Super User account for %s (%s) exists already.' % (username, email)))
