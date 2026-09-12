from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('schedevents', '0006_historicaleventplan_allow_maintain_rsvp_and_more'),
    ]

    operations = [
        # EventPlan
        migrations.AddField(
            model_name='eventplan',
            name='emoji_going',
            field=models.CharField(blank=True, default='✅', max_length=10),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='emoji_maybe',
            field=models.CharField(blank=True, default='❓', max_length=10),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='emoji_not_going',
            field=models.CharField(blank=True, default='❌', max_length=10),
        ),
        # HistoricalEventPlan (simple_history mirror)
        migrations.AddField(
            model_name='historicaleventplan',
            name='emoji_going',
            field=models.CharField(blank=True, default='✅', max_length=10),
        ),
        migrations.AddField(
            model_name='historicaleventplan',
            name='emoji_maybe',
            field=models.CharField(blank=True, default='❓', max_length=10),
        ),
        migrations.AddField(
            model_name='historicaleventplan',
            name='emoji_not_going',
            field=models.CharField(blank=True, default='❌', max_length=10),
        ),
        # EventTemplate
        migrations.AddField(
            model_name='eventtemplate',
            name='emoji_going',
            field=models.CharField(blank=True, default='✅', max_length=10),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='emoji_maybe',
            field=models.CharField(blank=True, default='❓', max_length=10),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='emoji_not_going',
            field=models.CharField(blank=True, default='❌', max_length=10),
        ),
    ]
