import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Pilot",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, unique=True)),
                ("team_fighting", models.PositiveSmallIntegerField(
                    default=0,
                    validators=[
                        django.core.validators.MinValueValidator(0),
                        django.core.validators.MaxValueValidator(5),
                    ],
                )),
                ("duelling", models.PositiveSmallIntegerField(
                    default=0,
                    validators=[
                        django.core.validators.MinValueValidator(0),
                        django.core.validators.MaxValueValidator(5),
                    ],
                )),
                ("leadership", models.PositiveSmallIntegerField(
                    default=0,
                    validators=[
                        django.core.validators.MinValueValidator(0),
                        django.core.validators.MaxValueValidator(5),
                    ],
                )),
                ("tags", models.JSONField(blank=True, default=list)),
                ("notes", models.TextField(blank=True, default="")),
                ("notes_updated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["name"],
                "permissions": [
                    ("view_pilot_roster", "Can view the pilot roster"),
                    ("manage_pilot_roster", "Can add/edit/delete pilots and goals"),
                ],
            },
        ),
        migrations.CreateModel(
            name="HistoricalPilot",
            fields=[
                ("id", models.IntegerField(blank=True, db_index=True)),
                ("name", models.CharField(max_length=255)),
                ("team_fighting", models.PositiveSmallIntegerField(default=0)),
                ("duelling", models.PositiveSmallIntegerField(default=0)),
                ("leadership", models.PositiveSmallIntegerField(default=0)),
                ("tags", models.JSONField(blank=True, default=list)),
                ("notes", models.TextField(blank=True, default="")),
                ("notes_updated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_by_id", models.IntegerField(blank=True, db_index=True, null=True)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(
                    choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")],
                    max_length=1,
                )),
                ("history_user", models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "historical pilot",
                "verbose_name_plural": "historical pilots",
                "ordering": ["-history_date", "-history_id"],
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="PilotGoal",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500)),
                ("completed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("pilot", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="goals",
                    to="pilotboard.pilot",
                )),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.CreateModel(
            name="HistoricalPilotGoal",
            fields=[
                ("id", models.IntegerField(blank=True, db_index=True)),
                ("title", models.CharField(max_length=500)),
                ("completed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("pilot_id", models.IntegerField(blank=True, db_index=True, null=True)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(
                    choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")],
                    max_length=1,
                )),
                ("history_user", models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "historical pilot goal",
                "verbose_name_plural": "historical pilot goals",
                "ordering": ["-history_date", "-history_id"],
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="GoalNote",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("goal", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="notes",
                    to="pilotboard.pilotgoal",
                )),
                ("author", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]
