# SEC-03: hash KillTracker API keys at rest.
# Adds key_hash/key_prefix, backfills from the existing plaintext `key` (so existing
# client keys keep validating), makes key_hash unique, then drops plaintext `key`
# from both the live table and the simple_history table.
import hashlib

from django.db import migrations, models


def backfill(apps, schema_editor):
    ApiKey = apps.get_model("killtracker", "ApiKey")
    for k in ApiKey.objects.all().iterator():
        if k.key:
            k.key_hash = hashlib.sha256(k.key.encode()).hexdigest()
            k.key_prefix = k.key[:8]
            k.save(update_fields=["key_hash", "key_prefix"])

    Historical = apps.get_model("killtracker", "HistoricalApiKey")
    for h in Historical.objects.all().iterator():
        if h.key:
            h.key_hash = hashlib.sha256(h.key.encode()).hexdigest()
            h.key_prefix = h.key[:8]
            h.save(update_fields=["key_hash", "key_prefix"])


def backfill_reverse(apps, schema_editor):
    # Irreversible at the data level: plaintext keys cannot be recovered from hashes.
    # Schema reversal re-adds the `key` column but it will be empty.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0004_alter_apikey_options_alter_blacklistentry_options_and_more"),
    ]

    operations = [
        # 1. Add new columns (nullable so the table can be populated first).
        migrations.AddField(
            model_name="apikey",
            name="key_hash",
            field=models.CharField(db_index=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="apikey",
            name="key_prefix",
            field=models.CharField(blank=True, default="", help_text="First chars of the raw key, for identification only.", max_length=12),
        ),
        migrations.AddField(
            model_name="historicalapikey",
            name="key_hash",
            field=models.CharField(db_index=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="historicalapikey",
            name="key_prefix",
            field=models.CharField(blank=True, default="", help_text="First chars of the raw key, for identification only.", max_length=12),
        ),
        # 2. Backfill hashes from the existing plaintext keys.
        migrations.RunPython(backfill, backfill_reverse),
        # 3. Enforce uniqueness / non-null now that every row is populated.
        migrations.AlterField(
            model_name="apikey",
            name="key_hash",
            field=models.CharField(db_index=True, max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name="historicalapikey",
            name="key_hash",
            field=models.CharField(db_index=True, max_length=64),
        ),
        # 4. Drop the plaintext key from the live table and the history table.
        migrations.RemoveField(model_name="apikey", name="key"),
        migrations.RemoveField(model_name="historicalapikey", name="key"),
    ]
