"""
Migration to physically create candidacy_squiretrialthread if it was missed.
The model was previously in orm_models and migrated to candidacy with --fake,
so the Django migration state has it recorded but the table was never created.
"""
from django.db import migrations


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS `candidacy_squiretrialthread` (
    `thread_id`    bigint       NOT NULL,
    `status`       varchar(4)   NOT NULL DEFAULT 'ACTV',
    `squire_id`    bigint       NOT NULL,
    `initiator_id` bigint       NULL,
    `started_at`   datetime(6)  NOT NULL,
    `closed_at`    datetime(6)  NULL,
    PRIMARY KEY (`thread_id`),
    KEY `candidacy_squiretrialthread_status_idx` (`status`),
    KEY `candidacy_squiretrialthread_closed_at_idx` (`closed_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('candidacy', '0009_alter_membershipapplicationtypegroup_id'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunSQL(sql=CREATE_SQL, reverse_sql=migrations.RunSQL.noop),
            ],
        ),
    ]
