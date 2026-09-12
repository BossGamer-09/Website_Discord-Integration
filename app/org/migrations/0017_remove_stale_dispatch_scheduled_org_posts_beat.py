from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("org", "0016_activitypingrole_min_rank"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DELETE FROM django_celery_beat_periodictask WHERE task = 'app.org.tasks.dispatch_scheduled_org_posts';",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
