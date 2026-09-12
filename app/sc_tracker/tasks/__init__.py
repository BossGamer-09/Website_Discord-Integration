"""
app/sc_tracker/tasks/__init__.py

Explicit imports ensure Celery's autodiscover_tasks finds these
even if the app uses a non-standard layout.
"""
from app.sc_tracker.tasks.rsi_status import (  # noqa
    cleanup_status_dms,
    dispatch_status_dms,
    poll_rsi_status,
)
from app.sc_tracker.tasks.hangar import (  # noqa
    sync_hangar_from_website,
    update_hangar_embed,
)