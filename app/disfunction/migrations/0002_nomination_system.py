"""
disfunction/migrations/0002_nomination_system.py

State-only migration: registers Nomination, DistinctionLevel, UserDistinction,
and EventReference under the disfunction app. Tables already exist as
orm_models_* and are pinned via db_table — no DDL is executed.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("disfunction", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[

                migrations.CreateModel(
                    name="Nomination",
                    fields=[
                        ("id",                   models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("nominee_id",           models.BigIntegerField(db_index=True)),
                        ("nominator_id",         models.BigIntegerField(db_index=True)),
                        ("nominee_username",     models.CharField(max_length=100)),
                        ("nominator_username",   models.CharField(max_length=100)),
                        ("event_date",           models.CharField(max_length=200)),
                        ("event_id",             models.CharField(blank=True, max_length=100, null=True)),
                        ("event_source",         models.CharField(
                            choices=[("MANUAL", "Manually entered"), ("ORGANIC", "Organic Event System"), ("SCHEDULED", "Scheduled Event")],
                            default="MANUAL", max_length=20,
                        )),
                        ("reason",               models.TextField()),
                        ("status",               models.CharField(
                            choices=[("PENDING", "Pending Approval"), ("APPROVED", "Approved - Nomination Awarded"), ("RECOGNIZED", "Approved - Recognition Granted"), ("DENIED", "Denied")],
                            default="PENDING", max_length=20,
                        )),
                        ("processed_by_id",       models.BigIntegerField(blank=True, null=True)),
                        ("processed_by_username", models.CharField(blank=True, max_length=100, null=True)),
                        ("denial_reason",         models.TextField(blank=True, null=True)),
                        ("created_at",            models.DateTimeField(auto_now_add=True)),
                        ("processed_at",          models.DateTimeField(blank=True, null=True)),
                        ("flags",                 models.JSONField(default=dict)),
                    ],
                    options={
                        "verbose_name": "Nomination",
                        "verbose_name_plural": "Nominations",
                        "ordering": ["-created_at"],
                        "db_table": "orm_models_nomination",
                        "indexes": [
                            models.Index(fields=["nominee_id", "status"],   name="orm_models__nominee_459cfd_idx"),
                            models.Index(fields=["nominator_id"],           name="orm_models__nominat_16fb26_idx"),
                            models.Index(fields=["status", "created_at"],   name="orm_models__status_dd1e8c_idx"),
                            models.Index(fields=["event_id"],               name="orm_models__event_i_77cb1a_idx"),
                        ],
                    },
                ),

                migrations.CreateModel(
                    name="DistinctionLevel",
                    fields=[
                        ("id",                   models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("level",                models.IntegerField(unique=True)),
                        ("nominations_required", models.IntegerField()),
                        ("role_id",              models.BigIntegerField()),
                        ("role_name",            models.CharField(max_length=100)),
                        ("description",          models.TextField(blank=True, null=True)),
                        ("color",                models.CharField(default="#0099FF", max_length=7)),
                        ("perks",                models.JSONField(default=list)),
                    ],
                    options={
                        "verbose_name": "Distinction Level",
                        "verbose_name_plural": "Distinction Levels",
                        "ordering": ["level"],
                        "db_table": "orm_models_distinctionlevel",
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(level__gte=1) & models.Q(level__lte=10),
                                name="distinction_level_range",
                            ),
                        ],
                    },
                ),

                migrations.CreateModel(
                    name="UserDistinction",
                    fields=[
                        ("id",                   models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("user_id",              models.BigIntegerField(db_index=True, unique=True)),
                        ("current_level",        models.IntegerField(default=0)),
                        ("total_nominations",    models.IntegerField(default=0)),
                        ("total_recognitions",   models.IntegerField(default=0)),
                        ("current_streak",       models.IntegerField(default=0)),
                        ("longest_streak",       models.IntegerField(default=0)),
                        ("last_nomination_date", models.DateTimeField(blank=True, null=True)),
                        ("nomination_rate",      models.FloatField(default=0.0)),
                        ("approval_rate",        models.FloatField(default=0.0)),
                        ("created_at",           models.DateTimeField(auto_now_add=True)),
                        ("updated_at",           models.DateTimeField(auto_now=True)),
                        ("last_level_up",        models.DateTimeField(blank=True, null=True)),
                    ],
                    options={
                        "verbose_name": "User Distinction",
                        "verbose_name_plural": "User Distinctions",
                        "db_table": "orm_models_userdistinction",
                        "indexes": [
                            models.Index(fields=["user_id", "current_level"], name="orm_models__user_id_e98e08_idx"),
                            models.Index(fields=["current_level"],            name="orm_models__current_6e0c19_idx"),
                            models.Index(fields=["total_nominations"],        name="orm_models__total_n_69f297_idx"),
                        ],
                    },
                ),

                migrations.CreateModel(
                    name="EventReference",
                    fields=[
                        ("id",                 models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("event_id",           models.CharField(db_index=True, max_length=100)),
                        ("source_system",      models.CharField(
                            choices=[
                                ("ORGANIC",     "Organic Event System"),
                                ("SCHEDULED",   "Scheduled Event System"),
                                ("MANUAL",      "Manual Entry"),
                                ("VC_TRACKING", "Voice Channel Tracking"),
                                ("ATTENDANCE",  "Attendance System"),
                            ],
                            max_length=20,
                        )),
                        ("event_name",         models.CharField(max_length=200)),
                        ("event_description",  models.TextField(blank=True, null=True)),
                        ("event_date",         models.DateTimeField()),
                        ("organizer_id",       models.BigIntegerField(blank=True, null=True)),
                        ("organizer_username", models.CharField(blank=True, max_length=100, null=True)),
                        ("participant_ids",    models.JSONField(default=list)),
                        ("metadata",           models.JSONField(default=dict)),
                        ("is_archived",        models.BooleanField(default=False)),
                        ("created_at",         models.DateTimeField(auto_now_add=True)),
                        ("updated_at",         models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "verbose_name": "Event Reference",
                        "verbose_name_plural": "Event References",
                        "db_table": "orm_models_eventreference",
                        "unique_together": {("event_id", "source_system")},
                        "indexes": [
                            models.Index(fields=["event_id", "source_system"], name="orm_models__event_i_e80aae_idx"),
                            models.Index(fields=["event_date"],                name="orm_models__event_d_f90234_idx"),
                            models.Index(fields=["organizer_id"],              name="orm_models__organiz_0266f1_idx"),
                        ],
                    },
                ),
            ],
        ),
    ]
