"""Les modèles Word livrés ne portent plus le logo d'URBIA mais une image
d'emplacement : l'installation d'URBIA reçoit son logo sur sa fiche, pour que
ses documents ne changent pas."""

from pathlib import Path

from django.core.files.base import ContentFile
from django.db import migrations

LOGO = Path(__file__).resolve().parent.parent / "reprise" / "logo-urbia.jpeg"


def reprendre(apps, schema_editor):
    Organisation = apps.get_model("core", "Organisation")
    organisation = Organisation.objects.filter(nom="URBIA Immobilier", logo="").first()
    if organisation:
        organisation.logo.save("logo-urbia.jpeg", ContentFile(LOGO.read_bytes()), save=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0004_aide_logo")]

    operations = [migrations.RunPython(reprendre, migrations.RunPython.noop)]
