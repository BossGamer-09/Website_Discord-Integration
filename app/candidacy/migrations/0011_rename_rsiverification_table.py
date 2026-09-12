"""
RSIVerification was created in orm_models but never deleted from there.
When candidacy/0002 ran with --fake, no physical table was created.
The DB has orm_models_rsiverification; Django now expects candidacy_rsiverification.
HistoricalRSIVerification was also fake-applied so its table doesn't exist either.

Both operations use RunPython to introspect first — safe to re-run and
works on MySQL 5.7 and 8.0.
"""
from django.db import migrations


def rename_main_table(apps, schema_editor):
    conn = schema_editor.connection
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'orm_models_rsiverification'")
        if cur.fetchone():
            cur.execute(
                "RENAME TABLE `orm_models_rsiverification` TO `candidacy_rsiverification`"
            )


def create_historical_table(apps, schema_editor):
    conn = schema_editor.connection
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'candidacy_historicalrsiverification'")
        if cur.fetchone():
            return  # already exists

        cur.execute("""
            CREATE TABLE `candidacy_historicalrsiverification` (
                `history_id`            int          NOT NULL AUTO_INCREMENT,
                `id`                    bigint       NOT NULL,
                `discord_id`            bigint       NOT NULL,
                `rsi_handle`            varchar(60)  NOT NULL,
                `rsi_profile_url`       varchar(200) NOT NULL,
                `verification_code`     varchar(20)  NOT NULL,
                `verification_status`   varchar(8)   NOT NULL,
                `expires_at`            datetime(6)  NOT NULL,
                `verified_at`           datetime(6)  NULL,
                `created_at`            datetime(6)  NOT NULL,
                `enlisted_date`         date         NULL,
                `org_membership`        varchar(100) NOT NULL DEFAULT '',
                `history_date`          datetime(6)  NOT NULL,
                `history_change_reason` varchar(100) NULL,
                `history_type`          varchar(1)   NOT NULL,
                PRIMARY KEY (`history_id`),
                KEY `cadidacy_hrsiverif_id_idx`           (`id`),
                KEY `cadidacy_hrsiverif_discord_id_idx`   (`discord_id`),
                KEY `cadidacy_hrsiverif_history_date_idx` (`history_date`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('candidacy', '0010_ensure_squiretrialthread_table'),
    ]

    operations = [
        migrations.RunPython(rename_main_table,      noop),
        migrations.RunPython(create_historical_table, noop),
    ]
