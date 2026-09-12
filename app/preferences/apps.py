from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PreferencesConfig(AppConfig):
    name = 'app.preferences'
    label = 'preferences'
    verbose_name = 'Preferences — User & Global Settings'
    default = True

    def ready(self):
        self.module.utils.autodiscover()
        self.module.utils.global_preferences_manager.on_ready()
        self.module.utils.user_preferences_manager.on_ready()

        post_migrate.connect(_auto_populate_preferences, sender=self)

        super().ready()


def _auto_populate_preferences(sender, **kwargs):
    from .utils import all_managers
    for manager in all_managers:
        manager.populate_db(remove_orphans=False)
