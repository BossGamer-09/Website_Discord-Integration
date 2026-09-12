from django.apps import AppConfig


class KillTrackerConfig(AppConfig):
    name = "app.killtracker"
    label = "killtracker"
    verbose_name = "Kill Tracker"

    def get_cogs(self):
        return ["app.killtracker.cogs.killtracker"]

    def ready(self):
        import app.killtracker.signals  # noqa
        import app.killtracker.tasks    # noqa
