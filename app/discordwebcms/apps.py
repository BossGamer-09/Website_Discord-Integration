from app.main.util.cog_loader import CogAwareAppConfig


class DiscordWebCMSConfig(CogAwareAppConfig):
    name = 'app.discordwebcms'
    label = "discordwebcms"
    verbose_name = "Knowledge Base (CMS)"
    default = True

    def ready(self):
        super().ready()
        import app.discordwebcms.signals  # noqa: F401 — registers @receiver decorators
