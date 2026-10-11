"""Calcule l'identité de chaque immeuble et réunit ceux qui ont été
enregistrés plusieurs fois : même nom à la même adresse, quelle que soit
l'écriture. Le plus ancien est gardé et reçoit les lots des autres. Aucune
propriété n'est supprimée ni modifiée, hormis son rattachement.

Le retour arrière ne recrée pas les fiches en double : elles ne portaient
rien d'autre que le nom et l'adresse du même immeuble.
"""

from django.db import migrations

from proprietes.models import cle_adresse


def identite(immeuble):
    return "|".join([cle_adresse(immeuble.adresse), (immeuble.code_postal or "").strip(), cle_adresse(immeuble.ville),
                     cle_adresse(immeuble.nom)])[:600]


def reunir(apps, schema_editor):
    Immeuble = apps.get_model("proprietes", "Immeuble")
    Propriete = apps.get_model("proprietes", "Propriete")
    gardes = {}
    for immeuble in Immeuble.objects.order_by("pk"):
        cle = identite(immeuble)
        garde = gardes.get(cle)
        if garde is None:
            gardes[cle] = immeuble
            Immeuble.objects.filter(pk=immeuble.pk).update(cle=cle)
        else:
            Propriete.objects.filter(immeuble_id=immeuble.pk).update(immeuble_id=garde.pk)
            immeuble.delete()


class Migration(migrations.Migration):
    dependencies = [("proprietes", "0014_immeuble_cle")]

    operations = [migrations.RunPython(reunir, migrations.RunPython.noop)]
