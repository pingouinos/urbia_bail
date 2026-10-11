"""Crée un contact par personne déjà présente et y rattache ses lignes.

Même règle que Contact.rattacher : même nom et même prénom, sans tenir compte
des majuscules ni des accents, sauf dates de naissance différentes.
"""

import unicodedata

from django.db import migrations


def normaliser(texte):
    sans_accents = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return " ".join(sans_accents.lower().split())


def rattacher(apps, schema_editor):
    Contact = apps.get_model("contacts", "Contact")
    sources = [
        # Les bailleurs d'abord : leur contact n'est pas automatique.
        (apps.get_model("proprietes", "Bailleur"), False),
        (apps.get_model("contrats", "Locataire"), True),
        (apps.get_model("candidatures", "Candidat"), True),
        (apps.get_model("candidatures", "Garant"), True),
    ]
    connus = {}
    for modele, automatique in sources:
        for ligne in modele.objects.filter(contact__isnull=True).order_by("pk"):
            prenom = getattr(ligne, "prenom", "") or ""
            cle = f"{normaliser(ligne.nom)}|{normaliser(prenom)}"[:255]
            naissance = getattr(ligne, "date_naissance", None)
            homonymes = connus.setdefault(cle, [])
            contact = next(
                (c for c in homonymes if not (c.date_naissance and naissance and c.date_naissance != naissance)),
                None,
            )
            if contact is None:
                contact = Contact(nom=ligne.nom, prenom=prenom, cle=cle, automatique=automatique)
                if getattr(ligne, "type", "physique") != "physique":
                    contact.nature = "entreprise"
                homonymes.append(contact)
            for champ in ("civilite", "date_naissance", "lieu_naissance", "email", "telephone", "adresse",
                          "code_postal", "ville"):
                valeur = getattr(ligne, champ, None)
                if valeur and not getattr(contact, champ):
                    setattr(contact, champ, valeur)
            contact.save()
            modele.objects.filter(pk=ligne.pk).update(contact=contact)


class Migration(migrations.Migration):
    dependencies = [
        ("contacts", "0001_contact"),
        ("proprietes", "0007_contact"),
        ("contrats", "0008_contact"),
        ("candidatures", "0007_contact"),
    ]

    operations = [migrations.RunPython(rattacher, migrations.RunPython.noop)]
