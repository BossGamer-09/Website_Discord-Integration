from django.db import migrations, models
from django.utils.text import slugify


def populate_slugs(apps, schema_editor):
    Pilot = apps.get_model("pilotboard", "Pilot")
    seen = set()
    for pilot in Pilot.objects.order_by("id"):
        base = slugify(pilot.name) or "pilot"
        slug, n = base, 2
        while slug in seen:
            slug = f"{base}-{n}"
            n += 1
        seen.add(slug)
        pilot.slug = slug
        pilot.save(update_fields=["slug"])


class Migration(migrations.Migration):

    dependencies = [
        ("pilotboard", "0005_alter_historicalpilot_options_and_more"),
    ]

    operations = [
        # Add nullable first so existing rows don't error
        migrations.AddField(
            model_name="pilot",
            name="slug",
            field=models.SlugField(max_length=255, unique=False, blank=True, default=""),
            preserve_default=False,
        ),
        migrations.RunPython(populate_slugs, migrations.RunPython.noop),
        # Enforce uniqueness after backfill
        migrations.AlterField(
            model_name="pilot",
            name="slug",
            field=models.SlugField(max_length=255, unique=True, blank=True),
        ),
        # Mirror in historical table
        migrations.AddField(
            model_name="historicalpilot",
            name="slug",
            field=models.SlugField(max_length=255, blank=True, default=""),
            preserve_default=False,
        ),
    ]
