from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse

from .google import BACKEND

# Formulaire du candidat retenu, accessible par son lien personnel. Seules
# pages servies sur l'adresse publique URL_LOCATAIRES.
CHEMINS_LOCATAIRES = ("/locataire/", "/static/")
# Pages accessibles sans être connecté.
CHEMINS_PUBLICS = ("/connexion/", "/oidc/", "/sante/", "/admin/login/", *CHEMINS_LOCATAIRES)
# Pages accessibles connecté mais avant la double authentification.
CHEMINS_MFA = ("/mfa/", "/deconnexion/")


class ConnexionObligatoireMiddleware:
    """Toute l'application est réservée aux collaborateurs connectés. Un compte
    local doit en plus valider son code TOTP (si MFA_OBLIGATOIRE) ; un compte
    Google a déjà fait sa double authentification chez Google."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        chemin = request.path
        if (settings.HOTE_LOCATAIRES and request.get_host().partition(":")[0] == settings.HOTE_LOCATAIRES
                and not chemin.startswith(CHEMINS_LOCATAIRES)):
            # Depuis Internet, le reste de l'application n'existe pas.
            raise Http404
        if chemin.startswith(CHEMINS_PUBLICS):
            return self.get_response(request)
        if not request.user.is_authenticated:
            return redirect(f"{reverse(settings.LOGIN_URL)}?next={chemin}")
        if (
            settings.MFA_OBLIGATOIRE
            and request.session.get(BACKEND_SESSION_KEY) != BACKEND
            and not request.user.is_verified()
            and not chemin.startswith(CHEMINS_MFA)
        ):
            return redirect(f"{reverse('mfa')}?next={chemin}")
        return self.get_response(request)
