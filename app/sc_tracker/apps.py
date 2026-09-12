"""
app/sc_tracker/apps.py
"""
from django.apps import AppConfig


class ScTrackerConfig(AppConfig):
    name = "app.sc_tracker"
    label = "sc_tracker"
    verbose_name = "SC Tracker — RSI Status"

    def get_cogs(self):
        return [
            "app.sc_tracker.cogs.hangar",
            "app.sc_tracker.cogs.rsi_status",
        ]

    def ready(self):
        import app.sc_tracker.tasks.rsi_status  # noqa
        import app.sc_tracker.tasks.hangar       # noqa
