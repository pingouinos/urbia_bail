from django.conf import settings

from .zones_tendues import lancer_si_besoin


class ZonesTenduesMiddleware:
    """Tient à jour la liste officielle des communes en zone tendue, sans
    tâche planifiée à configurer sur le serveur : la vérification se fait au
    fil des requêtes, le téléchargement en tâche de fond."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.ZONES_TENDUES_AUTO:
            lancer_si_besoin()
        return self.get_response(request)
