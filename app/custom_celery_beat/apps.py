from django_celery_beat.apps import BeatConfig

class CustomBeatConfig(BeatConfig):
    default_auto_field = 'django.db.models.AutoField'
