from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

from biens.models import Bien
from comptes.roles import est_administrateur


def accueil(request):
    return render(request, "core/accueil.html", {
        "est_admin": est_administrateur(request.user),
        "nb_biens": Bien.objects.filter(actif=True).count(),
    })


def sante(request):
    """Sonde pour Docker et le Synology : répond 200 si la base est joignable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"statut": "base indisponible"}, status=503)
    return JsonResponse({"statut": "ok"})
