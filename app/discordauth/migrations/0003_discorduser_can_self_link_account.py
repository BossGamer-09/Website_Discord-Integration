from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('discordauth', '0002_alter_discorduser_options'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='discorduser',
            options={'permissions': [
                ('can_link_discord_accounts', 'Can link Discord accounts to site accounts via bot command'),
                ('can_self_link_account', 'Can self-link own Discord account via bot command'),
            ]},
        ),
    ]
