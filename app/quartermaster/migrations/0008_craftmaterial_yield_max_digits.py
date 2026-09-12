from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('loot_tracker', '0007_craftblueprint_craftconstraint_craftmaterial_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='craftmaterial',
            name='yield_min',
            field=models.DecimalField(
                decimal_places=4,
                help_text='Minimum required quantity (e.g. 0.05 SCU, or 20 gems)',
                max_digits=10,
            ),
        ),
        migrations.AlterField(
            model_name='craftmaterial',
            name='yield_max',
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                help_text='Maximum required quantity. Leave blank if same as minimum.',
                max_digits=10,
                null=True,
            ),
        ),
    ]
