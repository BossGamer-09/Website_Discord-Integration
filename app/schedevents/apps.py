from app.main.util.cog_loader import CogAwareAppConfig


class SchedeventsConfig(CogAwareAppConfig):
    name = "app.schedevents"
    label = "schedevents"
    verbose_name = "Scheduled Events"
    default = True

    def ready(self):
        import app.schedevents.signals  # noqa: F401
        super().ready()
