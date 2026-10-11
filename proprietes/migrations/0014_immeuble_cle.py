from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("proprietes", "0013_associes")]

    operations = [
        migrations.AddField("immeuble", "cle", models.CharField(default="", editable=False, max_length=600)),
        migrations.AddField("historicalimmeuble", "cle", models.CharField(default="", editable=False, max_length=600)),
        migrations.AlterModelOptions("immeuble", {"ordering": ["ville", "adresse", "nom"], "verbose_name": "immeuble"}),
    ]
