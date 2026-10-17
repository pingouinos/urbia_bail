from django.db import migrations

NOMS = {"bail_habitation.docx": "bail_habitation", "mandat_gestion.docx": "mandat_gestion"}


def renommer(apps, schema_editor, sens=1):
    Modele = apps.get_model("documents", "Modele")
    for ancien, nouveau in NOMS.items():
        if sens < 0:
            ancien, nouveau = nouveau, ancien
        Modele.objects.filter(nom=ancien).update(nom=nouveau)


class Migration(migrations.Migration):
    """Les modèles passent du Word au HTML : leur nom perd l'extension .docx."""

    dependencies = [("documents", "0004_origine_editeur")]

    operations = [migrations.RunPython(renommer, lambda apps, editeur: renommer(apps, editeur, -1))]
