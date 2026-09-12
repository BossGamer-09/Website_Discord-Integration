"""
Safety-net migration: drops the legacy `verification_data` column (and any other
surviving legacy columns) from candidacy_rsiverification on MySQL.

Migration 0013 was already recorded as applied on production but the ALTER TABLE
never ran (likely due to the scorecard migration conflict blocking the migrate run).
This migration is a guaranteed second attempt — idempotent, MySQL-only.
"""
from django.db import migrations

LEGACY_COLUMNS = [
    'attempts',
    'last_attempt',
    'bio_screenshot',
    'discord_username',
    'verification_data',
    'verification_method',
]


def drop_legacy_columns(apps, schema_editor):
    conn = schema_editor.connection
    if conn.vendor != 'mysql':
        return

    table = 'candidacy_rsiverification'
    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        existing = {row[0] for row in cur.fetchall()}

        to_drop = [c for c in LEGACY_COLUMNS if c in existing]
        if to_drop:
            parts = ', '.join(f'DROP COLUMN `{c}`' for c in to_drop)
            cur.execute(f"ALTER TABLE `{table}` {parts}")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('candidacy', '0014_add_membershiprules_model'),
    ]

    operations = [
        migrations.RunPython(drop_legacy_columns, noop),
    ]
