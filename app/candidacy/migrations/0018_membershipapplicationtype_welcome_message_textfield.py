from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("candidacy", "0017_add_rulesagreement"),
    ]

    operations = [
        migrations.AlterField(
            model_name="membershipapplicationtype",
            name="welcome_message",
            field=models.TextField(
                blank=True,
                default=(
                    "**Induction: BlightVeil Legion**\n\n"
                    "{applicant} is no longer who they were.\n\n"
                    "Today, {applicant_name} enters the Legion.\n\n"
                    "In BlightVeil, we mark the day you commit to something greater than yourself.\n\n"
                    "This is the point where excuses die\n"
                    "and performance takes their place.\n\n"
                    "From this point forward:\n"
                    "Fight with purpose.\n"
                    "Commit to improvement.\n"
                    "Win.\n\n"
                    "Welcome to the Legion."
                ),
                help_text=(
                    "Message posted to the welcome channel on approval. "
                    "Supports: {applicant} {applicant_name} {rank_prefix} {rank_name} {discipline_channel}. "
                    "Leave blank to use the global MembershipWelcomeMessage preference."
                ),
            ),
        ),
    ]
