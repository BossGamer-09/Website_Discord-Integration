from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Create Async Task Schedule'

    def handle(self, *args, **options): # setup_periodic_tasks
        from django.utils.module_loading import autodiscover_modules
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        from ...utils import PERIODIC_TASKS

        autodiscover_modules('tasks')

        for path, data in PERIODIC_TASKS.items():
            every = data["every"]
            interval = data["interval"]
            name = data["name"] or path

            # executes every 10 seconds.
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=every,
                period=interval,
            )

            _, new = PeriodicTask.objects.get_or_create(task=path, defaults={
                "interval":schedule,
                "name":name,
            })

            self.stdout.write(self.style.SUCCESS("Create Async Task Schedule {}: {} {} {} New: {}".format(name, path, every, interval, new)))


"""
    IntervalSchedule.DAYS
    IntervalSchedule.HOURS
    IntervalSchedule.MINUTES
    IntervalSchedule.SECONDS
    IntervalSchedule.MICROSECONDS
"""
