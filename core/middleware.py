"""En-têtes de sécurité, limite de débit du formulaire ouvert au public, et
agence du compte connecté."""

from django.conf import settings
from django.contrib.auth import logout
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from .agence import activer
from .journal import adresse_ip

# Les pages n'appellent que l'application elle-même et, pour l'aide à la
# saisie des adresses, deux services de l'État. Les scripts et styles en
# ligne des gabarits imposent « unsafe-inline ».
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "connect-src 'self' https://data.geopf.fr https://geo.api.gouv.fr",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
FENETRE = 600  # secondes


class SecuriteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        public = request.path.startswith("/locataire/")
        if public and self.trop_de_demandes(request):
            reponse = HttpResponse(
                "Trop de demandes depuis votre connexion. Merci de réessayer dans quelques minutes.",
                status=429, content_type="text/plain; charset=utf-8",
            )
            reponse["Retry-After"] = str(FENETRE)
        else:
            reponse = self.get_response(request)
        if reponse.get("Content-Type", "").startswith("text/html"):
            # Sur les pages seulement : un PDF ouvert dans le navigateur garde son lecteur intégré.
            reponse.setdefault("Content-Security-Policy", CSP)
            reponse.setdefault("Permissions-Policy", PERMISSIONS)
        if public:
            # Le lien personnel du candidat ne doit fuir ni par l'en-tête Referer ni par un cache.
            reponse["Referrer-Policy"] = "no-referrer"
        if not request.path.startswith("/static/"):
            # Des données personnelles : rien n'est gardé par le navigateur ou un proxy.
            reponse.setdefault("Cache-Control", "private, no-store")
        return reponse

    @staticmethod
    def trop_de_demandes(request):
        """Vrai au-delà de LIMITE_FORMULAIRE_PUBLIC demandes en dix minutes
        depuis une même adresse (0 : pas de limite)."""
        limite = settings.LIMITE_FORMULAIRE_PUBLIC
        if not limite:
            return False
        cle = f"debit-locataire:{adresse_ip(request)}"
        if cache.add(cle, 1, FENETRE):
            return False
        try:
            return cache.incr(cle) > limite
        except ValueError:  # la clé a expiré entre les deux appels
            cache.add(cle, 1, FENETRE)
            return False


# Le superutilisateur exploite la plateforme : il n'appartient à aucune agence
# et ne voit les données d'aucune : seulement l'espace Plateforme et la console.
CHEMINS_SUPERUTILISATEUR = (
    "/plateforme/", "/admin/", "/deconnexion/", "/mfa/", "/connexion/", "/oidc/", "/static/", "/sante/",
)


class AgenceMiddleware:
    """Pendant la requête, `Organisation.courante()` est l'agence du compte
    connecté."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .models import Membre

        agence = None
        if request.user.is_authenticated and request.user.is_superuser:
            if not request.path.startswith(CHEMINS_SUPERUTILISATEUR):
                return redirect("plateforme")
        elif request.user.is_authenticated:
            membre = Membre.objects.select_related("organisation").filter(utilisateur=request.user).first()
            agence = membre.organisation if membre else None
            if agence and not agence.active:
                # Désactivée par l'exploitant, même en pleine session.
                logout(request)
                return redirect(f"{reverse(settings.LOGIN_URL)}?agence=desactivee")
        with activer(agence):
            return self.get_response(request)
