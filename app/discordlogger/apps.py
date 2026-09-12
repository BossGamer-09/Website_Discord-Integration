from app.main.util.cog_loader import CogAwareAppConfig


class DiscordLoggerConfig(CogAwareAppConfig):
    name = 'app.discordlogger'
    label = "discordlogger"
    verbose_name = "Discord Logger — Voice & Messages"
    default = True

    def ready(self):
        super().ready()
