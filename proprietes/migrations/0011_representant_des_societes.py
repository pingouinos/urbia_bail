"""Pour une société, le prénom et la civilité saisis avec la raison sociale
étaient ceux de la personne qui la représente : ils passent au représentant."""

from django.db import migrations


def separer(apps, schema_editor):
    Bailleur = apps.get_model("proprietes", "Bailleur")
    Contact = apps.get_model("contacts", "Contact")
    for bailleur in Bailleur.objects.exclude(type="physique").exclude(prenom="", civilite=""):
        bailleur.representant_civilite = bailleur.representant_civilite or bailleur.civilite
        bailleur.representant_prenom = bailleur.representant_prenom or bailleur.prenom
        bailleur.civilite = bailleur.prenom = ""
        bailleur.save()
        if bailleur.contact_id:
            # Le contact de la société ne porte plus que sa raison sociale.
            contact = Contact.objects.get(pk=bailleur.contact_id)
            contact.prenom, contact.civilite, contact.nature = "", "", "entreprise"
            contact.cle = contact.cle.split("|")[0] + "|"
            contact.save()


class Migration(migrations.Migration):
    dependencies = [
        ("proprietes", "0010_controles_et_representant"),
        ("contacts", "0002_rattacher_les_personnes"),
    ]

    operations = [migrations.RunPython(separer, migrations.RunPython.noop)]
