from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_membres"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalorganisation",
            name="active",
            field=models.BooleanField(
                default=True,
                help_text="Une agence désactivée garde ses données, mais ses comptes ne peuvent plus se connecter.",
            ),
        ),
        migrations.AddField(
            model_name="organisation",
            name="active",
            field=models.BooleanField(
                default=True,
                help_text="Une agence désactivée garde ses données, mais ses comptes ne peuvent plus se connecter.",
            ),
        ),
    ]
