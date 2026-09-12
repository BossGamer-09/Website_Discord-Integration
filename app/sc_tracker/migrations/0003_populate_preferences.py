"""
app/sc_tracker/migrations/0003_populate_preferences.py

Seeds GlobalSetting rows for all @global_preference classes in
app.sc_tracker.preferences so they appear in Django admin immediately.

Beat schedules are NOT seeded here — run:
    python manage.py createsched
after deploy to register them via the app.celerytools system.
"""
from django.db import migrations


def populate_preferences(apps, schema_editor):
    from app.preferences.utils import global_preferences_manager
    import app.sc_tracker.preferences  # noqa — registers @global_preference classes

    GlobalSetting = apps.get_model("preferences", "GlobalSetting")

    for import_path, cls in global_preferences_manager.registry.items():
        if not import_path.startswith("app.sc_tracker.preferences."):
            continue
        GlobalSetting.objects.get_or_create(
            linked_module_class_path=import_path,
            defaults={
                "name":        getattr(cls, "initial_name", import_path.split(".")[-1]),
                "description": getattr(cls, "initial_description", ""),
            },
        )


def depopulate_preferences(apps, schema_editor):
    GlobalSetting = apps.get_model("preferences", "GlobalSetting")
    GlobalSetting.objects.filter(
        linked_module_class_path__startswith="app.sc_tracker.preferences."
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sc_tracker", "0002_sctrackerconfig_and_more"),
        ("preferences", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(populate_preferences, reverse_code=depopulate_preferences),
    ]
