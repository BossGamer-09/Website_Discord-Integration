from django.utils.module_loading import autodiscover_modules
from app.main.util.cog_loader import CogAwareAppConfig


class OrgConfig(CogAwareAppConfig):
    name = 'app.org'
    label = "org"
    verbose_name = "Org — Members & Roles"
    default = True

    def ready(self):
        autodiscover_modules('signals')
        super().ready()
