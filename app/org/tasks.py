from datetime import datetime

from celery import shared_task
from celery.utils.log import get_task_logger
from django_celery_beat.models import IntervalSchedule
from app.celerytools import register_periodic_task
from celery_once import QueueOnce
from django.apps import apps

logger = get_task_logger(__name__)


@shared_task(base=QueueOnce, bind=True)
@register_periodic_task(2, IntervalSchedule.HOURS)
def refresh_guild_roles(task):
    logger.info("refresh_guild_roles started")
    DiscordRole = apps.get_model(app_label='org', model_name='DiscordRole')

    DiscordRole.update_roles_in_db_from_api()

