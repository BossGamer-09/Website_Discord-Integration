from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('disfunction', '0010_protectedchannelack_voicechannelprofile_required_group'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='voiceban',
            options={'permissions': [('can_manage_voice_bans', 'Can manage voice bans via bot commands')]},
        ),
    ]
