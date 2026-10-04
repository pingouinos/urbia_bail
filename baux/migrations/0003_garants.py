# Plusieurs garants : les champs « garant » deviennent « garants », une caution
# par ligne. Renommage puis changement de type, pour garder les valeurs déjà
# saisies.

from django.db import migrations, models

GARANTS_COMMUNS = models.TextField(
    blank=True,
    help_text="Nom et adresse de chaque caution qui garantit tous les locataires, une par ligne.",
    verbose_name="garants communs",
)
GARANTS_LOCATAIRE = models.TextField(
    blank=True,
    help_text="Nom et adresse de chaque caution de ce locataire, une par ligne.",
)


class Migration(migrations.Migration):

    dependencies = [
        ("baux", "0002_colocation"),
    ]

    operations = [
        migrations.RenameField(model_name="bail", old_name="garant", new_name="garants"),
        migrations.RenameField(model_name="historicalbail", old_name="garant", new_name="garants"),
        migrations.RenameField(model_name="locataire", old_name="garant", new_name="garants"),
        migrations.RenameField(model_name="historicallocataire", old_name="garant", new_name="garants"),
        migrations.AlterField(model_name="bail", name="garants", field=GARANTS_COMMUNS),
        migrations.AlterField(model_name="historicalbail", name="garants", field=GARANTS_COMMUNS),
        migrations.AlterField(model_name="locataire", name="garants", field=GARANTS_LOCATAIRE),
        migrations.AlterField(model_name="historicallocataire", name="garants", field=GARANTS_LOCATAIRE),
    ]
