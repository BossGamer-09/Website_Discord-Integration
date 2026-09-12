"""
Replace the auto-named (hash-truncated) M2M table for
MembershipApplicationType.on_accept_gives_permission_groups with an explicit
through model that has a stable, short db_table name.

The old table `candidacy_membershipapplicationtype_on_accept_gives_permissi????`
did not exist in production (Django version hash mismatch), so we just create
the new table. No data migration needed — the M2M was never usable.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("candidacy", "0007_membershipapplicationrecord_reset_status"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # --- Database: create the new explicit table ---
            database_operations=[
                migrations.CreateModel(
                    name="MembershipApplicationTypeGroup",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                        (
                            "membershipapplicationtype",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                to="candidacy.membershipapplicationtype",
                            ),
                        ),
                        (
                            "group",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                to="auth.group",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "candidacy_membershiptype_groups",
                        "unique_together": {("membershipapplicationtype", "group")},
                    },
                ),
            ],
            # --- State: record both the new through model and the updated M2M field ---
            state_operations=[
                migrations.CreateModel(
                    name="MembershipApplicationTypeGroup",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                        (
                            "membershipapplicationtype",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                to="candidacy.membershipapplicationtype",
                            ),
                        ),
                        (
                            "group",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                to="auth.group",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "candidacy_membershiptype_groups",
                        "unique_together": {("membershipapplicationtype", "group")},
                    },
                ),
                migrations.AlterField(
                    model_name="membershipapplicationtype",
                    name="on_accept_gives_permission_groups",
                    field=models.ManyToManyField(
                        blank=True,
                        through="candidacy.MembershipApplicationTypeGroup",
                        related_name="given_by_membership_type",
                        to="auth.group",
                        help_text="Permission groups automatically added to the user on approval.",
                    ),
                ),
            ],
        ),
    ]
