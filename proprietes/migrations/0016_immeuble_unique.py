from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("proprietes", "0015_immeubles_en_double")]

    operations = [
        migrations.AddConstraint("immeuble", models.UniqueConstraint(fields=["cle"], name="immeuble_unique")),
    ]
