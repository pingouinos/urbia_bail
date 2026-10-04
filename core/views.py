from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

from baux.models import Bail
from biens.models import Bien
from candidatures.models import Candidature
from comptes.roles import est_administrateur
from mandats.models import Mandat


def accueil(request):
    """Page d'accueil centrée sur la rédaction des baux : derniers baux et
    candidatures retenues qui attendent le leur."""
    return render(request, "core/accueil.html", {
        "est_admin": est_administrateur(request.user),
        "derniers_baux": Bail.objects.select_related("bien").prefetch_related("locataires").order_by("-modifie_le")[:8],
        "a_rediger": Candidature.objects.filter(statut=Candidature.Statut.RETENUE, bail__isnull=True)
        .select_related("bien").prefetch_related("candidats"),
        "nb_biens": Bien.objects.filter(actif=True).count(),
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
