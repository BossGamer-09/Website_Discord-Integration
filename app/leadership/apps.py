from app.main.util.cog_loader import CogAwareAppConfig


class LeadershipConfig(CogAwareAppConfig):
    name = 'app.leadership'
    label = 'leadership'
    verbose_name = "Leadership — Discipline & Ranks"
    default = True

    def ready(self):
        import app.leadership.signals  # noqa
        super().ready()
