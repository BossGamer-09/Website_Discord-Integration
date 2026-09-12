"""
Data migration: delete the auto-generated add/change/delete/view Permission rows
for all project models that now have default_permissions = ().

After setting default_permissions = () on models, Django stops creating NEW CRUD
perms on migrate, but it does NOT retroactively delete the existing rows.  This
migration does that cleanup so the admin permission picker is no longer polluted.

Only project app models are touched; Django auth, guardian, admin, contenttypes,
celery, otp_*, qsessions rows are left alone.
"""
from django.db import migrations

PROJECT_APPS = [
    "disfunction",
    "candidacy",
    "schedevents",
    "killtracker",
    "discordlogger",
    "inventory",
    "sc_tracker",
    "pilotboard",
    "unifieduser",
    "org",
    "scorecard",
    "discordwebcms",
    "goals",
    "mailclient",
    "leadership",
    "orgevents",
]

CRUD_PREFIXES = ("add_", "change_", "delete_", "view_")


def delete_crud_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    cts = ContentType.objects.filter(app_label__in=PROJECT_APPS)
    deleted = (
        Permission.objects
        .filter(content_type__in=cts)
        .filter(
            codename__startswith="add_"
        )
        .union(
            Permission.objects.filter(content_type__in=cts).filter(codename__startswith="change_"),
            Permission.objects.filter(content_type__in=cts).filter(codename__startswith="delete_"),
            Permission.objects.filter(content_type__in=cts).filter(codename__startswith="view_"),
        )
    )
    # union() returns a non-deletable queryset; filter instead
    count, _ = (
        Permission.objects
        .filter(content_type__in=cts)
        .filter(
            codename__regex=r"^(add|change|delete|view)_"
        )
        .delete()
    )
    print(f"\n  Deleted {count} auto-generated CRUD permission rows.")


class Migration(migrations.Migration):

    dependencies = [
        ('orm_models', '0023_cleanup_stale_content_types'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(
            delete_crud_permissions,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
