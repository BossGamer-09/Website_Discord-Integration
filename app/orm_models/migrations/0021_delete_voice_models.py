from django.db import migrations


class Migration(migrations.Migration):
    """
    State-only migration: removes all voice/attendance models from the
    'orm_models' app state. Tables are NOT dropped — they are now owned by
    app.disfunction (see disfunction/migrations/0001_initial.py).
    """

    dependencies = [
        ("orm_models",   "0020_delete_userjoinrecord"),
        ("disfunction",  "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="EventAttendanceRecord"),
                migrations.DeleteModel(name="EventAttendance"),
                migrations.DeleteModel(name="VoiceActivitySummary"),
                migrations.DeleteModel(name="VoiceSessionCheckpoint"),
                migrations.DeleteModel(name="VoiceSession"),
                migrations.DeleteModel(name="HistoricalVoiceBan"),
                migrations.DeleteModel(name="VoiceBan"),
                migrations.DeleteModel(name="TemporaryVoiceChannel"),
            ],
        ),
    ]
