from django.db import connection
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from contrats.models import Bail
from proprietes.models import Propriete
from candidatures.models import Candidature
from comptes.roles import est_administrateur
from mandats.models import Mandat


def locations_en_cours():
    """Logements dont la mise en location demande une action, avec l'étape
    atteinte et la prochaine chose à faire : préavis à relouer, candidatures à
    étudier, bail à rédiger, bail à faire signer."""
    lignes = {}

    def ligne(propriete):
        return lignes.setdefault(propriete.pk, {"propriete": propriete, "a_etudier": 0, "retenue": None, "a_signer": None, "preavis": None})

    candidatures = Candidature.objects.filter(
        bail__isnull=True, statut__in=[Candidature.Statut.A_ETUDIER, Candidature.Statut.RETENUE],
    ).select_related("propriete").prefetch_related("candidats")
    for candidature in candidatures:
        if candidature.statut == Candidature.Statut.RETENUE:
            ligne(candidature.propriete)["retenue"] = candidature
        else:
            ligne(candidature.propriete)["a_etudier"] += 1
    baux = Bail.objects.filter(date_fin_effective__isnull=True).filter(
        Q(date_signature__isnull=True) | Q(conge_recu_le__isnull=False)
    ).select_related("propriete").prefetch_related("locataires")
    for bail in baux:
        ligne(bail.propriete)["preavis" if bail.conge_recu_le else "a_signer"] = bail
    for propriete in Propriete.objects.filter(mise_en_location_le__isnull=False, actif=True):
        ligne(propriete)

    for l in lignes.values():
        propriete = l["propriete"]
        if l["preavis"]:
            l["detail"] = f"Libre le {l['preavis'].date_depart_prevue:%d/%m/%Y}"
        else:
            l["detail"] = f"Disponible le {propriete.disponible_le:%d/%m/%Y}" if propriete.a_louer and propriete.disponible_le else ""
        if l["a_signer"]:
            l["etape"] = f"Bail à faire signer : {l['a_signer'].noms_locataires or 'locataire à renseigner'}"
            l["action"], l["lien"] = "Ouvrir le bail", l["a_signer"].get_absolute_url()
        elif l["retenue"]:
            l["etape"] = f"Candidat retenu : {l['retenue'].noms or 'candidature'}"
            l["action"], l["lien"] = "Rédiger le bail", f"{reverse('contrats:creer')}?candidature={l['retenue'].pk}"
        elif l["a_etudier"]:
            nombre = l["a_etudier"]
            l["etape"] = f"{nombre} candidature{'s' if nombre > 1 else ''} à étudier"
            l["action"], l["lien"] = "Étudier", f"{reverse('candidatures:liste')}?propriete={propriete.pk}&statut=a_etudier"
        elif propriete.a_louer:
            l["etape"] = "À louer, aucune candidature"
            l["action"], l["lien"] = "Enregistrer une candidature", f"{reverse('candidatures:creer')}?propriete={propriete.pk}"
        else:
            l["etape"] = "Départ annoncé"
            l["action"], l["lien"] = "Mettre en location", reverse("proprietes:mettre_en_location", args=[propriete.pk])
    return sorted(lignes.values(), key=lambda l: (l["propriete"].ville, l["propriete"].adresse))


def accueil(request):
    """Page d'accueil centrée sur les mises en location en cours, puis les
    derniers baux."""
    return render(request, "core/accueil.html", {
        "est_admin": est_administrateur(request.user),
        "derniers_baux": Bail.objects.select_related("propriete").prefetch_related("locataires").order_by("-modifie_le")[:8],
        "locations": locations_en_cours(),
        "nb_proprietes": Propriete.objects.filter(actif=True).count(),
        "nb_baux": Bail.objects.filter(date_fin_effective__isnull=True).count(),
        "nb_candidatures": Candidature.objects.filter(statut=Candidature.Statut.A_ETUDIER).count(),
        "nb_mandats": Mandat.objects.filter(date_fin__isnull=True).count(),
    })


def sante(request):
    """Sonde pour Docker et le Synology : répond 200 si la base est joignable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"statut": "base indisponible"}, status=503)
    return JsonResponse({"statut": "ok"})
