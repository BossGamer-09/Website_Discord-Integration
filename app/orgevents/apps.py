from app.main.util.cog_loader import CogAwareAppConfig


class OrgeventsConfig(CogAwareAppConfig):
    name = 'app.orgevents'
    label = 'orgevents'
    verbose_name = "⚠️ LEGACY — Orgevents (do not use)"
    default_auto_field = 'django.db.models.BigAutoField'
