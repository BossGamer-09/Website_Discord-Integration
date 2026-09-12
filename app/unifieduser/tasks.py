from celery import shared_task
from celery.utils.log import get_task_logger
from celery_once import QueueOnce
from django_celery_beat.models import IntervalSchedule

from app.celerytools import register_periodic_task

logger = get_task_logger(__name__)


@shared_task(base=QueueOnce, once={"graceful": True})
@register_periodic_task(6, IntervalSchedule.HOURS)
def rebuild_display_name_cache():
    """
    Walk all active OrgPlayers with their discorduser loaded and write
    DisplayNameSearchCache so the web roster shows real names instead of
    the internal discorduid.xxx username format.
    """
    from app.unifieduser.models import OrgPlayer, DisplayNameSearchCache

    qs = (
        OrgPlayer.objects
        .filter(is_active=True)
        .select_related("discorduser", "rank")
        .exclude(username="AnonymousUser")
    )

    updated = 0
    for player in qs.iterator(chunk_size=200):
        try:
            du = player.discorduser
        except Exception:
            du = None

        if du:
            name = du.main_guild_nick or du.global_name or du.full_username or player.username
        else:
            name = player.username

        # Apply rank prefix if source=4 is configured
        try:
            from app.preferences.models import UserSetting
            from app.unifieduser.preferences import DisplayNameSource, CustomDisplayName
            src = UserSetting.get_setting_value(DisplayNameSource.import_path, player)
            if src == 4:
                cdn = UserSetting.get_setting_value(CustomDisplayName.import_path, player)
                if cdn:
                    name = f"{player.rank.prefix} {cdn}".strip() if player.rank_id else cdn
        except Exception:
            pass

        DisplayNameSearchCache.objects.update_or_create(
            user=player,
            defaults={"display_name": name},
        )
        updated += 1

    logger.info("rebuild_display_name_cache: updated %d entries", updated)
    return updated
