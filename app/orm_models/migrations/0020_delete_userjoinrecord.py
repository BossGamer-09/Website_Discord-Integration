from django.db import migrations


class Migration(migrations.Migration):
    """
    State-only migration: removes UserJoinRecord from the 'orm_models' app state.
    The table is NOT dropped — it is now owned by app.org (see org/migrations/0005).
    """

    dependencies = [
        ("orm_models", "0019_delete_devtestlog_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name='UserJoinRecord'),
            ],
        ),
    ]
