from django.db import migrations


class Migration(migrations.Migration):
    """
    State-only: removes UserJoinRecord from the org app state.
    Table is NOT dropped — now owned by app.disfunction.
    """

    dependencies = [
        ("org",         "0005_userjoinrecord"),
        ("disfunction", "0003_welcome_system"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="UserJoinRecord"),
            ],
        ),
    ]
