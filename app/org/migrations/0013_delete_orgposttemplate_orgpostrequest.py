from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('org', '0012_alter_activitypingportal_options_and_more'),
    ]

    operations = [
        migrations.DeleteModel(name='OrgPostRequest'),
        migrations.DeleteModel(name='OrgPostTemplate'),
    ]
