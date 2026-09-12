from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("candidacy", "0006_assignablediscipline"),
    ]

    operations = [
        migrations.AlterField(
            model_name="membershipapplicationrecord",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending Review"),
                    ("APPROVED", "Approved"),
                    ("DENIED", "Denied"),
                    ("RESET", "Reset"),
                ],
                db_index=True,
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="historicalmembershipapplicationrecord",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending Review"),
                    ("APPROVED", "Approved"),
                    ("DENIED", "Denied"),
                    ("RESET", "Reset"),
                ],
                db_index=True,
                default="PENDING",
                max_length=20,
            ),
        ),
    ]
