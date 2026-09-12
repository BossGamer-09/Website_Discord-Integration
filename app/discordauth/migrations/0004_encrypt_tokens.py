# app/discordauth/migrations/0004_encrypt_tokens.py
# SEC-06: encrypt Discord OAuth tokens at rest.
#
# Converts access_token/refresh_token from CharField(unique) to EncryptedTextField, then
# re-saves every row so existing plaintext is encrypted in place. from_db_value tolerates
# legacy plaintext, so the read-then-save loop converts cleanly with no flag-day.
#
# REQUIRES settings.TOKEN_ENC_KEY to be set (in .env). manage.py will refuse to start without
# it, which is intentional: it prevents this migration from running half-way. Back up the
# discorduser table and run on a copy before production.
from django.db import migrations

import app.discordauth.fields


def encrypt_existing(apps, schema_editor):
    DiscordUser = apps.get_model("discordauth", "DiscordUser")
    for du in DiscordUser.objects.all().iterator():
        # from_db_value returns plaintext for legacy rows; save() re-encrypts via get_prep_value.
        if du.access_token or du.refresh_token:
            du.save(update_fields=["access_token", "refresh_token"])


def noop(apps, schema_editor):
    # Reverse reverts the column type but does NOT decrypt data. Restore from backup if needed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("discordauth", "0003_discorduser_can_self_link_account"),
    ]

    operations = [
        migrations.AlterField(
            model_name="discorduser",
            name="access_token",
            field=app.discordauth.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="discorduser",
            name="refresh_token",
            field=app.discordauth.fields.EncryptedTextField(blank=True, null=True),
        ),
        migrations.RunPython(encrypt_existing, noop),
    ]
