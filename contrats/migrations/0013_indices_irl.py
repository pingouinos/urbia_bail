from decimal import Decimal

from django.db import migrations

# IRL publiés par l'INSEE (France hors Corse et outre-mer), série 001515333.
INDICES = {
    2022: ("133.93", "135.84", "136.27", "137.26"),
    2023: ("138.61", "140.59", "141.03", "142.06"),
    2024: ("143.46", "145.17", "144.51", "144.64"),
    2025: ("145.47", "146.68", "145.77", "145.78"),
    2026: ("146.60", "148.37", "148.06"),
}


def charger(apps, schema_editor):
    IndiceIRL = apps.get_model("contrats", "IndiceIRL")
    for annee, valeurs in INDICES.items():
        for trimestre, valeur in enumerate(valeurs, start=1):
            IndiceIRL.objects.get_or_create(annee=annee, trimestre=trimestre, defaults={"valeur": Decimal(valeur)})


class Migration(migrations.Migration):
    dependencies = [("contrats", "0012_echeances")]

    operations = [migrations.RunPython(charger, migrations.RunPython.noop)]
