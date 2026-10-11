"""Réunit les bailleurs enregistrés plusieurs fois : même raison sociale
pour une société (sauf SIREN différents), même nom et même prénom pour une
personne (sauf codes postaux différents). Le plus ancien est gardé : il
reçoit les propriétés, les documents et les associés des autres, et les
renseignements qui lui manquaient. Aucune propriété n'est supprimée.

Le retour arrière ne recrée pas les fiches en double.
"""

import re

from django.db import migrations

from proprietes.models import cle_adresse

COMPLETER = (
    "civilite", "conjoint_civilite", "conjoint_nom", "conjoint_prenom", "ref_ics", "adresse", "code_postal", "ville",
    "email", "telephone", "siren", "representant_civilite", "representant_nom", "representant_prenom",
    "representant_qualite", "representant_id", "contact_id",
)


def distincts(a, b):
    a, b = re.sub(r"\s", "", a or ""), re.sub(r"\s", "", b or "")
    return bool(a and b and a != b)


def reunir(apps, schema_editor):
    Bailleur = apps.get_model("proprietes", "Bailleur")
    Propriete = apps.get_model("proprietes", "Propriete")
    Piece = apps.get_model("proprietes", "Piece")
    Associe = apps.get_model("proprietes", "Associe")
    gardes = []
    for bailleur in Bailleur.objects.order_by("pk"):
        societe = bailleur.type != "physique"
        cle = societe, cle_adresse(bailleur.nom), "" if societe else cle_adresse(bailleur.prenom)
        garde = next((
            g for g in gardes
            if g.cle == cle and not (distincts(g.siren, bailleur.siren) if societe
                                     else distincts(g.code_postal, bailleur.code_postal))
        ), None)
        if garde is None:
            bailleur.cle = cle
            gardes.append(bailleur)
            continue
        complete = False
        for champ in COMPLETER:
            if not getattr(garde, champ) and getattr(bailleur, champ):
                setattr(garde, champ, getattr(bailleur, champ))
                complete = True
        # La référence ICS est unique : on la libère avant de la reporter.
        Bailleur.objects.filter(pk=bailleur.pk).update(ref_ics=None)
        if complete:
            garde.save()
        Propriete.objects.filter(bailleur_id=bailleur.pk).update(bailleur_id=garde.pk)
        Piece.objects.filter(bailleur_id=bailleur.pk).update(bailleur_id=garde.pk)
        connus = {(cle_adresse(a.nom), cle_adresse(a.prenom)) for a in Associe.objects.filter(bailleur_id=garde.pk)}
        for associe in Associe.objects.filter(bailleur_id=bailleur.pk):
            if (cle_adresse(associe.nom), cle_adresse(associe.prenom)) in connus:
                associe.delete()
            else:
                Associe.objects.filter(pk=associe.pk).update(bailleur_id=garde.pk)
        bailleur.delete()


class Migration(migrations.Migration):
    dependencies = [("proprietes", "0016_immeuble_unique"), ("contacts", "0004_societes_sans_civilite")]

    operations = [migrations.RunPython(reunir, migrations.RunPython.noop)]
