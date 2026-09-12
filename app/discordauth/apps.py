from app.main.util.cog_loader import CogAwareAppConfig


class DiscordauthConfig(CogAwareAppConfig):
    name = 'app.discordauth'
    label = "discordauth"
    verbose_name = "Discord Auth"
    default = True

    def ready(self):
        super().ready()
