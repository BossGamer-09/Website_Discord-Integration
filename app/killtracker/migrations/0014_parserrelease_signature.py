from django.db import migrations, models


_HELP = (
    "Hex Ed25519 signature of the source, made with the OFFLINE parser-signing key "
    "(tools/sign_parser_release.py in the Killtracker repo). Clients that ship a "
    "public key refuse any release whose signature doesn't verify — so a compromised "
    "server/admin account can't push code to users' machines."
)


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0013_seed_game_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="parserrelease",
            name="signature",
            field=models.CharField(blank=True, default="", help_text=_HELP, max_length=128),
        ),
        migrations.AddField(
            model_name="historicalparserrelease",
            name="signature",
            field=models.CharField(blank=True, default="", help_text=_HELP, max_length=128),
        ),
    ]
