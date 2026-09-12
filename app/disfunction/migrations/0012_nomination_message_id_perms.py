from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('disfunction', '0011_voiceban_can_manage_voice_bans'),
    ]

    operations = [
        migrations.AddField(
            model_name='nomination',
            name='approval_message_id',
            field=models.BigIntegerField(null=True, blank=True, help_text="Discord message ID of the approval embed"),
        ),
        migrations.AddField(
            model_name='nomination',
            name='approval_channel_id',
            field=models.BigIntegerField(null=True, blank=True),
        ),
        migrations.AlterModelOptions(
            name='nomination',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Nomination',
                'verbose_name_plural': 'Nominations',
                'permissions': [
                    ('can_approve_nominations', 'Can approve or deny nominations'),
                    ('can_submit_nominations', 'Can submit nominations for members'),
                ],
            },
        ),
    ]
