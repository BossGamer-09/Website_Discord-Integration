import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("candidacy", "0018_membershipapplicationtype_welcome_message_textfield"),
        ("unifieduser", "__first__"),
    ]

    operations = [
        migrations.AddField(
            model_name="membershipapplicationtype",
            name="on_promote_rank_pk",
            field=models.PositiveIntegerField(
                null=True,
                blank=True,
                help_text=(
                    "PK of the OrgRank to assign when staff clicks 'Promote' after trial approval "
                    "(e.g. Legion Member). Leave blank to hide the Promote button."
                ),
            ),
        ),
        migrations.AddField(
            model_name="membershipapplicationrecord",
            name="promoted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="promoted_membership_apps",
                to="unifieduser.orgplayer",
            ),
        ),
        migrations.AddField(
            model_name="membershipapplicationrecord",
            name="promoted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="historicalmembershipapplicationrecord",
            name="promoted_by_id",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="historicalmembershipapplicationrecord",
            name="promoted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
