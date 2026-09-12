from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('schedevents', '0011_remove_require_approval'),
    ]

    operations = [
        # New VC-unlock mentions field
        migrations.AddField(
            model_name='eventplan',
            name='mentions_on_vc_unlock',
            field=models.JSONField(blank=True, default=list,
                help_text='Discord role IDs (ints) to ping when the event VC unlocks (~30m before start).'),
        ),
        migrations.AddField(
            model_name='historicaleventplan',
            name='mentions_on_vc_unlock',
            field=models.JSONField(blank=True, default=list,
                help_text='Discord role IDs (ints) to ping when the event VC unlocks (~30m before start).'),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='mentions_on_vc_unlock',
            field=models.JSONField(blank=True, default=list),
        ),

        # Bump emoji field width so Discord custom emoji format "<:name:id>" fits
        migrations.AlterField(
            model_name='eventplan',
            name='emoji_going',
            field=models.CharField(blank=True, default='✅', max_length=64),
        ),
        migrations.AlterField(
            model_name='eventplan',
            name='emoji_maybe',
            field=models.CharField(blank=True, default='❓', max_length=64),
        ),
        migrations.AlterField(
            model_name='eventplan',
            name='emoji_not_going',
            field=models.CharField(blank=True, default='❌', max_length=64),
        ),
        migrations.AlterField(
            model_name='historicaleventplan',
            name='emoji_going',
            field=models.CharField(blank=True, default='✅', max_length=64),
        ),
        migrations.AlterField(
            model_name='historicaleventplan',
            name='emoji_maybe',
            field=models.CharField(blank=True, default='❓', max_length=64),
        ),
        migrations.AlterField(
            model_name='historicaleventplan',
            name='emoji_not_going',
            field=models.CharField(blank=True, default='❌', max_length=64),
        ),
        migrations.AlterField(
            model_name='eventtemplate',
            name='emoji_going',
            field=models.CharField(blank=True, default='✅', max_length=64),
        ),
        migrations.AlterField(
            model_name='eventtemplate',
            name='emoji_maybe',
            field=models.CharField(blank=True, default='❓', max_length=64),
        ),
        migrations.AlterField(
            model_name='eventtemplate',
            name='emoji_not_going',
            field=models.CharField(blank=True, default='❌', max_length=64),
        ),
    ]
