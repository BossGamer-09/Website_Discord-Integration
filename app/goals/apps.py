from app.main.util.cog_loader import CogAwareAppConfig


class GoalsConfig(CogAwareAppConfig):
    name = "app.goals"
    label = "goals"
    verbose_name = "Goals — Org & Leader Goals"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        super().ready()
        import app.goals.signals  # noqa: F401
