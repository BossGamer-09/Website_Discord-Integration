from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sc_tracker', '0006_alter_hangarstatusmessage_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sctrackerconfig',
            name='updates_channel_id',
            field=models.BigIntegerField(
                default=0,
                help_text='Discord channel ID where active incident embeds are posted (Star Citizen Updates).',
            ),
        ),
    ]
