from app.main.util.cog_loader import CogAwareAppConfig


class OrmModelsConfig(CogAwareAppConfig):
    name = 'app.orm_models'
    label = "orm_models"
    verbose_name = "⚠️ LEGACY — Orm Models (do not use)"
    default = True
