from app.main.util.cog_loader import CogAwareAppConfig


class CandidacyConfig(CogAwareAppConfig):
    name = 'app.candidacy'
    label = "candidacy"
    verbose_name = "Candidacy — Applications & Trials"
    default = True

    def ready(self):
        super().ready()
