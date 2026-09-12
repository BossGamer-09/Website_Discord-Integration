from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sc_tracker", "0004_alter_sctrackerconfig_hangar_patch_info_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="hangarstatusmessage",
            name="phase_notification_message_id",
            field=models.BigIntegerField(null=True, blank=True),
        ),
    ]
