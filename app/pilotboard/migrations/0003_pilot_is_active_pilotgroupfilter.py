import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("pilotboard", "0002_pilot_org_player"),
    ]

    operations = [
        migrations.AddField(
            model_name="pilot",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.CreateModel(
            name="PilotGroupFilter",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(
                    blank=True,
                    help_text="Override display label (leave blank to use group name)",
                    max_length=100,
                )),
                ("order", models.PositiveSmallIntegerField(
                    default=0,
                    help_text="Display order (lower = first)",
                )),
                ("group", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="+",
                    to="auth.group",
                )),
            ],
            options={
                "verbose_name": "Roster Group Filter",
                "verbose_name_plural": "Roster Group Filters",
                "ordering": ["order", "group__name"],
            },
        ),
    ]
