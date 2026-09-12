from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('org', '0006_delete_userjoinrecord'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='discordrole',
            options={
                'ordering': ['-discord_order'],
                'permissions': [('can_manage_discord_members', 'Can manage Discord members via bot commands')],
            },
        ),
    ]
