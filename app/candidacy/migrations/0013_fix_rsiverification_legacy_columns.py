"""
candidacy_rsiverification was renamed from orm_models_rsiverification (0011).
That original table has legacy columns not in the current model, some without
DB defaults which cause INSERT failures in MySQL strict mode.

Uses RunPython to introspect existing columns/indexes before issuing DDL —
safe on both MySQL 5.7 and 8.0, and idempotent (re-runnable).
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

LEGACY_INDEXES = [
    'orm_models__discord_079265_idx',
    'orm_models__discord_db5dfe_idx',
    'orm_models__verific_856700_idx',
    'orm_models__verific_b3555f_idx',
    'orm_models__expires_2c0289_idx',
    'orm_models__expires_4e18c3_idx',
]


def fix_rsiverification_table(apps, schema_editor):
    conn = schema_editor.connection
    if conn.vendor != 'mysql':
        return  # legacy columns only exist on the MySQL production DB
    table = 'candidacy_rsiverification'

    with conn.cursor() as cur:
        # --- Introspect existing columns ---
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        existing_cols = {row[0] for row in cur.fetchall()}

        cols_to_drop = [c for c in LEGACY_COLUMNS if c in existing_cols]
        if cols_to_drop:
            drop_parts = ', '.join(f'DROP COLUMN `{c}`' for c in cols_to_drop)
            cur.execute(f"ALTER TABLE `{table}` {drop_parts}")

        # --- Introspect existing indexes ---
        cur.execute(f"SHOW INDEX FROM `{table}`")
        existing_indexes = {row[2] for row in cur.fetchall()}  # row[2] = Key_name

        for idx in LEGACY_INDEXES:
            if idx in existing_indexes:
                cur.execute(f"DROP INDEX `{idx}` ON `{table}`")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('candidacy', '0012_add_missing_rsiverification_columns'),
    ]

    operations = [
        migrations.RunPython(fix_rsiverification_table, noop),
    ]
