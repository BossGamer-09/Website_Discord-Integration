from app.main.util.cog_loader import CogAwareAppConfig


class PilotboardConfig(CogAwareAppConfig):
    name = "app.pilotboard"
    label = "pilotboard"
    verbose_name = "Pilot Roster"
    default = True

    def ready(self):
        import app.pilotboard.signals  # noqa: F401
        super().ready()
