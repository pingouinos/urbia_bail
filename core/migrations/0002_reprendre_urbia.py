from django.db import migrations

# Coordonnées qui étaient écrites dans les modèles Word et dans le code avant
# que l'agence ait sa fiche.
URBIA = {
    "nom": "URBIA Immobilier",
    "raison_sociale": "URBIA",
    "forme_juridique": "SAS",
    "capital": 1000,
    "siren": "823 373 873",
    "rcs_ville": "Toulouse",
    "adresse": "302 avenue de Muret",
    "code_postal": "31300",
    "ville": "Toulouse",
    "telephone": "05 32 26 15 25",
    "email": "contact@urbia-immobilier.fr",
    "carte_pro": "CPI 3101 2017 000 016 956",
    "carte_delivree_par": "la CCI de la Haute-Garonne",
    "garant": "GALIAN",
    "garant_adresse": "89 rue La Boétie, 75008 Paris",
    "garantie_montant": 1360000,
}


def reprendre(apps, schema_editor):
    """Une installation déjà en service est celle d'URBIA : sa fiche reprend
    ce que portaient ses documents. Une installation neuve part d'une fiche
    vide, à remplir par son administrateur."""
    Organisation = apps.get_model("core", "Organisation")
    Utilisateur = apps.get_model("auth", "User")
    if Utilisateur.objects.exists() and not Organisation.objects.exists():
        Organisation.objects.create(**URBIA)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_organisation"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(reprendre, migrations.RunPython.noop)]
