"""Efface l'historique des contacts automatiques déjà supprimés : avant cette
version, la purge des candidatures en laissait une copie (nom, e-mail,
adresse, naissance) dans l'historique."""

from django.db import migrations


def effacer(apps, schema_editor):
    Contact = apps.get_model("contacts", "Contact")
    Historique = apps.get_model("contacts", "HistoricalContact")
    Historique.objects.filter(automatique=True).exclude(id__in=Contact.objects.values("id")).delete()


class Migration(migrations.Migration):
    dependencies = [("contacts", "0004_societes_sans_civilite")]

    operations = [migrations.RunPython(effacer, migrations.RunPython.noop)]
