from django.utils.module_loading import autodiscover_modules
from app.main.util.cog_loader import CogAwareAppConfig


class DisfunctionConfig(CogAwareAppConfig):
    name = 'app.disfunction'
    label = 'disfunction'
    verbose_name = 'Nominations, Voice & Attendance'
    default = True

    def ready(self):
        autodiscover_modules('signals')
        super().ready()
