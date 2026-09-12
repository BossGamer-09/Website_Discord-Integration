from app.main.util.cog_loader import CogAwareAppConfig


class InfantryboardConfig(CogAwareAppConfig):
    name = "app.infantryboard"
    label = "infantryboard"
    verbose_name = "Infantry Roster"
    default = True

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import m2m_changed

        import app.infantryboard.signals  # noqa: F401

        # Auto-create Soldier entry when user gains view_infantry_roster permission via a group
        from app.infantryboard.signals import on_user_groups_changed
        User = get_user_model()
        m2m_changed.connect(on_user_groups_changed, sender=User.groups.through)

        super().ready()

    def get_cogs(self):
        return [
            "app.infantryboard.cogs.infantry_commands",
        ]
