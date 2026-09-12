from app.main.util.cog_loader import CogAwareAppConfig


class SpecialtyConfig(CogAwareAppConfig):
    name         = "app.specialty"
    label        = "specialty"
    verbose_name = "Specialty Granting"
    default      = False

    def ready(self):
        import app.specialty.signals  # noqa
        super().ready()
