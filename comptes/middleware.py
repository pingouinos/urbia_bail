from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

# Pages accessibles sans être connecté.
CHEMINS_PUBLICS = ("/connexion/", "/sante/", "/static/", "/admin/login/")
# Pages accessibles connecté mais avant la double authentification.
CHEMINS_MFA = ("/mfa/", "/deconnexion/")


class ConnexionObligatoireMiddleware:
    """Toute l'application est réservée aux collaborateurs connectés et,
    si MFA_OBLIGATOIRE, ayant validé leur code de double authentification."""

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
            and not request.user.is_verified()
            and not chemin.startswith(CHEMINS_MFA)
        ):
            return redirect(f"{reverse('mfa')}?next={chemin}")
        return self.get_response(request)
