from django.db import migrations


class Migration(migrations.Migration):
    """
    State-only: removes Nomination, DistinctionLevel, UserDistinction, EventReference
    from the orm_models app state. Tables are NOT dropped — owned by app.disfunction.
    """

    dependencies = [
        ("orm_models",  "0021_delete_voice_models"),
        ("disfunction", "0002_nomination_system"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="Nomination"),
                migrations.DeleteModel(name="DistinctionLevel"),
                migrations.DeleteModel(name="UserDistinction"),
                migrations.DeleteModel(name="EventReference"),
            ],
        ),
    ]
