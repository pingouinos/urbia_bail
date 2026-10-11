"""Le contact d'une société ne porte ni civilité, ni prénom, ni naissance :
ce qui en restait d'une saisie antérieure est effacé."""

from django.db import migrations


def nettoyer(apps, schema_editor):
    Contact = apps.get_model("contacts", "Contact")
    for contact in Contact.objects.filter(nature="entreprise"):
        if contact.civilite or contact.prenom or contact.date_naissance or contact.lieu_naissance:
            contact.civilite = contact.prenom = contact.lieu_naissance = ""
            contact.date_naissance = None
            contact.cle = contact.cle.split("|")[0] + "|"
            contact.save()


class Migration(migrations.Migration):
    dependencies = [("contacts", "0003_controles_et_representant")]

    operations = [migrations.RunPython(nettoyer, migrations.RunPython.noop)]
