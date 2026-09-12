from app.main.util.cog_loader import CogAwareAppConfig


class MeritsConfig(CogAwareAppConfig):
    name = "app.merits"
    label = "merits"
    verbose_name = "Merits & Money Requests"
    default = False

    def ready(self):
        import app.merits.signals  # noqa
        super().ready()
