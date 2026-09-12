"""
candidacy_rsiverification was renamed from orm_models_rsiverification in 0011.
That original table is missing enlisted_date and org_membership which were
added via candidacy/0005 before the table physically existed.

Uses RunPython to introspect before adding — safe on MySQL 5.7 and 8.0.
"""
from django.db import migrations


def add_missing_columns(apps, schema_editor):
    conn = schema_editor.connection
    table = 'candidacy_rsiverification'

    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        existing = {row[0] for row in cur.fetchall()}

        parts = []
        if 'enlisted_date' not in existing:
            parts.append("ADD COLUMN `enlisted_date` date NULL")
        if 'org_membership' not in existing:
            parts.append("ADD COLUMN `org_membership` varchar(100) NOT NULL DEFAULT ''")

        if parts:
            cur.execute(f"ALTER TABLE `{table}` {', '.join(parts)}")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('candidacy', '0011_rename_rsiverification_table'),
    ]

    operations = [
        migrations.RunPython(add_missing_columns, noop),
    ]
