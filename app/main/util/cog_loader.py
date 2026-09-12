import os
import pkgutil
from django.apps import AppConfig


class CogAwareAppConfigMixin:
    """
    Mixin to Load and find cogs in <appname>/cogs/<....>.py
    """
    def __init__(self, app_name, app_module):
        self._cogs = []
        super().__init__(app_name, app_module)

    def get_cogs(self):
        return self._cogs

    def register_app_cogs(self):
        cogs_dir_path = os.path.join(self.path, 'cogs')

        if os.path.isdir(cogs_dir_path):
            for module_info in pkgutil.iter_modules([cogs_dir_path]):
                cog_name = module_info.name

                if cog_name.startswith('_'):
                    continue

                full_import_path = f"{self.name}.cogs.{cog_name}"

                self._cogs.append(full_import_path)

    def ready(self):
        self.register_app_cogs()
        return super().ready()


class CogAwareAppConfig(CogAwareAppConfigMixin, AppConfig):
    pass
