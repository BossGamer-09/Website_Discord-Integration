from celery import shared_task
from celery.utils.log import get_task_logger
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from celery_once import QueueOnce
from django_celery_beat.models import PeriodicTask, IntervalSchedule
from app.celerytools import register_periodic_task

from app.discordauth.models import DiscordUser
from .models import LoggedVoiceStateSnapshot, LoggedVoiceState, CompiledLoggedVoiceState


celery_logger = get_task_logger(__name__)


@shared_task(base=QueueOnce, once={'graceful': True}, bind=True)
@register_periodic_task(1, IntervalSchedule.DAYS)
def prune_old_messages_task(task):
    """Delete LoggedDiscordMessage rows older than 3 months. Runs daily."""
    from .models import LoggedDiscordMessage
    cutoff = timezone.now() - timedelta(days=90)
    deleted, _ = LoggedDiscordMessage.objects.filter(created_at__lt=cutoff).delete()
    celery_logger.info("Pruned %d LoggedDiscordMessage rows older than 90 days", deleted)


@shared_task(base=QueueOnce, once={'timeout': 60*17}, bind=True)
@register_periodic_task(30, IntervalSchedule.MINUTES)
def compile_voice_states_task(task):
    batch_size = 500

    while True:
        len_compiled = compile_voice_states(task, batch_size)

        if not len_compiled:
            break


def compile_voice_states(task, batch_size):
    # 1. Fetch the uncompiled snapshots chronologically
    snapshots = list(
        LoggedVoiceStateSnapshot.objects.filter(compiled=False)
        .order_by('timestamp')[:batch_size]
    )

    if not snapshots:
        celery_logger.info("No uncompiled snapshots found.")
        return 0

    # 2. Fetch the VERY LAST compiled snapshot to act as our bridge/anchor
    # This fulfills your requirement to use the most recent compiled data
    # to seamlessly connect the previous batch to this new batch.
    last_compiled_snapshot = LoggedVoiceStateSnapshot.objects.filter(compiled=True).order_by('-timestamp').first()

    all_snapshots_to_fetch = snapshots.copy()
    if last_compiled_snapshot:
        all_snapshots_to_fetch.insert(0, last_compiled_snapshot)

    # Pre-fetch all logs for the current batch + the anchor snapshot
    logs = LoggedVoiceState.objects.filter(
        for_snapshot__in=all_snapshots_to_fetch
    ).select_related('for_snapshot')

    # --- AUTO-CREATE MISSING USERS ---
    # 1. Get every unique user ID in this batch
    all_uids_in_batch = {log.discorduser_id for log in logs}

    # 2. Ask the DB which of these IDs actually exist
    existing_uids = set(
        DiscordUser.objects.filter(pk__in=all_uids_in_batch).values_list('pk', flat=True)
    )

    # 3. Find the difference
    missing_uids = all_uids_in_batch - existing_uids

    if missing_uids:
        # Bulk create the missing users.
        # like username, though usually just the ID is enough for a stub).
        users_to_create = [DiscordUser(pk=uid) for uid in missing_uids]
        DiscordUser.objects.bulk_create(users_to_create, ignore_conflicts=True)

        celery_logger.info(f"Auto-created {len(missing_uids)} missing DiscordUsers.")
    # ------------------------------------------

    logs_by_time = {}
    for log in logs:
        ts = log.for_snapshot.timestamp
        if ts not in logs_by_time:
            logs_by_time[ts] = []
        logs_by_time[ts].append(log)

    active_intervals = {}
    intervals_to_create = []
    intervals_to_update = []

    def states_match(log, compiled_state):
        return (
            log.channel_id == compiled_state.channel_id and
            log.self_mute == compiled_state.self_mute and
            log.self_deaf == compiled_state.self_deaf and
            log.server_mute == compiled_state.server_mute and
            log.server_deaf == compiled_state.server_deaf and
            log.self_stream == compiled_state.self_stream and
            log.self_video == compiled_state.self_video
        )

    with transaction.atomic():
        # If we have an anchor snapshot, we need to fetch the actively open compiled blocks
        # from the database so we can update them rather than creating duplicates.
        if last_compiled_snapshot:
            open_blocks = CompiledLoggedVoiceState.objects.filter(
                interval_stop_at=last_compiled_snapshot.timestamp
            )
            for block in open_blocks:
                active_intervals[block.discorduser_id] = block

        # --- THE COMPILATION ENGINE ---
        for snapshot in snapshots:
            current_time = snapshot.timestamp
            current_logs = logs_by_time.get(current_time, [])
            users_in_snapshot = set()

            for log in current_logs:
                uid = log.discorduser_id
                users_in_snapshot.add(uid)

                if uid in active_intervals:
                    active_state = active_intervals[uid]

                    # --- GAP DETECTION LOGIC ---
                    # We check the time difference between the last known stop and now.
                    time_since_last = current_time - active_state.interval_stop_at

                    # We add a small 5-second buffer for processing/network jitter
                    max_allowed_gap = snapshot.expected_resolution + timedelta(seconds=5)

                    if states_match(log, active_state) and time_since_last <= max_allowed_gap:
                        # Bot was online, state is identical. Extend the timeline!
                        active_state.interval_stop_at = current_time

                        # Only add to update list if it's an existing DB row from a previous batch
                        if active_state.id and active_state not in intervals_to_update:
                            intervals_to_update.append(active_state)
                    else:
                        # BREAK IN CONTINUITY!
                        # Either the user changed state (muted/unmuted) OR the gap was too large
                        # because the bot crashed. We finalize the old block.
                        if not active_state.id:
                            intervals_to_create.append(active_state)

                        # Start a fresh block at the current timestamp
                        active_intervals[uid] = CompiledLoggedVoiceState(
                            discorduser_id=uid, channel_id=log.channel_id,
                            interval_start_at=current_time, interval_stop_at=current_time,
                            self_mute=log.self_mute, self_deaf=log.self_deaf,
                            server_mute=log.server_mute, server_deaf=log.server_deaf,
                            self_stream=log.self_stream, self_video=log.self_video
                        )
                else:
                    # User wasn't in active dict, start a new block
                    active_intervals[uid] = CompiledLoggedVoiceState(
                        discorduser_id=uid, channel_id=log.channel_id,
                        interval_start_at=current_time, interval_stop_at=current_time,
                        self_mute=log.self_mute, self_deaf=log.self_deaf,
                        server_mute=log.server_mute, server_deaf=log.server_deaf,
                        self_stream=log.self_stream, self_video=log.self_video
                    )

            # Drop users who left the channel entirely
            dropped_users = set(active_intervals.keys()) - users_in_snapshot
            for uid in dropped_users:
                closed_state = active_intervals.pop(uid)
                if not closed_state.id:
                    intervals_to_create.append(closed_state)

        # Flush remaining new active intervals
        for uid, state in active_intervals.items():
            if not state.id:
                intervals_to_create.append(state)

        # --- BULK DATABASE OPERATIONS ---
        if intervals_to_create:
            CompiledLoggedVoiceState.objects.bulk_create(intervals_to_create)
        if intervals_to_update:
            CompiledLoggedVoiceState.objects.bulk_update(intervals_to_update, ['interval_stop_at'])

        # --- GARBAGE COLLECTION (Your Requested Feature) ---
        # 1. Identify the very last snapshot in this batch to serve as the anchor for the next run
        new_anchor_snapshot = snapshots[-1]

        # 2. Mark it as compiled so it doesn't get pulled into the `snapshots` list next time
        new_anchor_snapshot.compiled = True
        new_anchor_snapshot.save(update_fields=['compiled'])

        # 3. Nuke everything else! Delete all previously compiled snapshots,
        # plus all snapshots in this batch EXCEPT the new anchor.
        # Thanks to on_delete=models.CASCADE, this automatically deletes all associated LoggedVoiceStates!
        LoggedVoiceStateSnapshot.objects.filter(
            timestamp__lt=new_anchor_snapshot.timestamp
        ).delete()

    snapshot_count = len(snapshots)

    celery_logger.info(f"Compiled {snapshot_count} snapshots. Database cleaned.")
    return snapshot_count
