from django.apps import AppConfig


class UnifieduserConfig(AppConfig):
    name = 'app.unifieduser'
    label = "unifieduser"
    verbose_name = "Members — Ranks & Roster"

    def ready(self):
        super().ready()
