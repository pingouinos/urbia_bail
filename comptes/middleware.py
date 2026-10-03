from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY
from django.shortcuts import redirect
from django.urls import reverse

from .google import BACKEND

# Pages accessibles sans être connecté.
CHEMINS_PUBLICS = ("/connexion/", "/oidc/", "/sante/", "/static/", "/admin/login/")
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
