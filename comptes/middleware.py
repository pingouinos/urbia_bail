from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

# Pages accessibles sans être connecté.
CHEMINS_PUBLICS = ("/connexion/", "/sante/", "/static/", "/admin/login/")


class ConnexionObligatoireMiddleware:
    """Toute l'application est réservée aux collaborateurs connectés."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not request.path.startswith(CHEMINS_PUBLICS):
            return redirect(f"{reverse(settings.LOGIN_URL)}?next={request.path}")
        return self.get_response(request)
