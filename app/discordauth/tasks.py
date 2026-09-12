import traceback
import random
from math import ceil
from datetime import datetime, timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django_celery_beat.models import PeriodicTask, IntervalSchedule
from django.core.cache import cache
from app.celerytools import register_periodic_task
from celery_once import QueueOnce
from django.apps import apps

from app.main.util.misc import utcnow_aware


celery_logger = get_task_logger(__name__)


@shared_task(base=QueueOnce, bind=True)
@register_periodic_task(30, IntervalSchedule.MINUTES)
def refresh_discord_cache(task):
    celery_logger.info("refresh_discord_cache started")
    DiscordUser = apps.get_model(app_label='discordauth', model_name='DiscordUser')

    total_approx = DiscordUser.objects.exclude(access_token__isnull=True).count()

    current_page = cache.get("discorduid:next-cache-page", default=random.randint(0, 11))

    per_page = ceil(total_approx / 10)

    start = per_page*current_page
    stop = per_page*(current_page+1)

    qs = DiscordUser.objects.exclude(access_token__isnull=True)[start:stop]

    if not qs:
        current_page = 0

        start = per_page*current_page
        stop = per_page*(current_page+1)

        qs = DiscordUser.objects.exclude(access_token__isnull=True)[start:stop]

    cache.set("discorduid:next-cache-page", current_page+1, timeout=timedelta(days=5).total_seconds())

    for discorduser in qs:
        try:
            discorduser.refresh_cache(force=False)
        except Exception as exc:
            traceback.print_exception(exc)


@shared_task(base=QueueOnce, once={'timeout': 60}, bind=True)
def refresh_discorduser_cache_single(task, discorduid, force=False):
    celery_logger.info("refresh_discorduser_cache_single discorduid=%s force=%s", discorduid, force)
    DiscordUser = apps.get_model(app_label='discordauth', model_name='DiscordUser')

    try:
        discorduser = DiscordUser.objects.get(discorduid=discorduid)
        discorduser.refresh_cache(force=force)
    except Exception as exc:
        traceback.print_exception(exc)


@shared_task(base=QueueOnce, bind=True)
@register_periodic_task(2, IntervalSchedule.HOURS)
def refresh_discord_oauth_tokens(task):
    celery_logger.info("refresh_discord_oauth_tokens started")
    DiscordUser = apps.get_model(app_label='discordauth', model_name='DiscordUser')

    qs = DiscordUser.objects.filter(access_token_expires__lte=utcnow_aware()+timedelta(days=1))

    for discorduser in qs:
        try:
            discorduser.token_refresh()
        except Exception as exc:
            traceback.print_exception(exc)


@shared_task(base=QueueOnce, bind=True)
def refresh_discord_roles_linked_groups(task, discorduid, force_refresh_cache=False):
    celery_logger.info("refresh_discord_roles_linked_groups discorduid=%s", discorduid)
    DiscordUser = apps.get_model(app_label='discordauth', model_name='DiscordUser')

    try:
        discorduser = DiscordUser.objects.get(discorduid=discorduid)
        discorduser.sync_discord_roles_linked_groups(raise_if_http_required=False, force_refresh_cache=force_refresh_cache)
    except Exception as exc:
        traceback.print_exception(exc)
