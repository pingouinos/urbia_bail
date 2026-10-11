from django.db import migrations


class Migration(migrations.Migration):
    """La candidature suit le renommage du bien en propriété. Le « contrat »
    d'un candidat (CDI, étudiant, retraité…) devient sa « situation » : le mot
    contrat désigne désormais les baux et les mandats."""

    dependencies = [
        ("candidatures", "0004_nombre_locataires"),
        ("proprietes", "0005_renommage_propriete"),
    ]

    operations = [
        migrations.RenameField(model_name="candidature", old_name="bien", new_name="propriete"),
        migrations.RenameField(model_name="candidat", old_name="contrat", new_name="situation"),
    ]
