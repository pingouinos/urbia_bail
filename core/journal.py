"""Journal de sécurité : qui a fait quoi, quand et d'où.

Il trace ce que l'historique des fiches ne dit pas : connexions, ouvertures
de documents, exports, suppressions, changements de droits. Il se consulte
dans l'administration et s'écrit aussi dans les journaux du serveur.
"""

import logging

from django.conf import settings

journal = logging.getLogger("securite")


def adresse_ip(request):
    """Adresse du poste. Derrière le proxy de production, c'est la dernière
    adresse ajoutée à X-Forwarded-For, la seule que le client ne choisit pas."""
    if settings.HTTPS:
        suivies = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if suivies:
            return suivies.split(",")[-1].strip()[:45]
    return request.META.get("REMOTE_ADDR", "")[:45]


def noter(request, action, objet="", detail="", identifiant=""):
    """Inscrit un événement. Ne fait jamais échouer l'action qu'il décrit."""
    from .models import Evenement

    utilisateur = getattr(request, "user", None)
    utilisateur = utilisateur if utilisateur is not None and utilisateur.is_authenticated else None
    identifiant = identifiant or (utilisateur.get_username() if utilisateur else "")
    ip = adresse_ip(request) if request is not None else ""
    journal.info("%s | %s | %s | %s | %s", action, identifiant or "-", ip or "-", objet, detail)
    try:
        Evenement.objects.create(
            utilisateur=utilisateur, identifiant=identifiant[:150], ip=ip, action=action[:40],
            objet=str(objet)[:200], detail=str(detail)[:300],
        )
    except Exception:  # la base indisponible ne doit pas bloquer l'utilisateur
        journal.exception("Événement non enregistré : %s", action)
