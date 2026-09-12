from app.main.util.cog_loader import CogAwareAppConfig


class QuartermasterConfig(CogAwareAppConfig):
    name = "app.quartermaster"
    label = "loot_tracker"
    verbose_name = "Quartermaster"
    default = True

    def ready(self):
        import app.quartermaster.signals  # noqa
        super().ready()
