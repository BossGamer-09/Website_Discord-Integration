from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('disfunction', '0008_vcrolepermission'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='voicechannelprofile',
            name='permissions_source_channel_id',
        ),
    ]
