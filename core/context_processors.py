from django.conf import settings
from django.utils.functional import SimpleLazyObject

from comptes.roles import est_administrateur

from .models import Organisation

# Pages de l'administration, pour mettre son entrée de menu en évidence.
ADMINISTRATION = ("administration", "agence", "journal", "utilisateurs", "utilisateur_creer", "utilisateur_modifier")


def application(request):
    return {
        "nom_application": settings.NOM_APPLICATION,
        "rubrique": rubrique(request),
        "est_administrateur": SimpleLazyObject(
            lambda: request.user.is_authenticated and est_administrateur(request.user)
        ),
        # Lue seulement par les pages qui l'affichent.
        "agence": SimpleLazyObject(Organisation.courante),
    }


def rubrique(request):
    """Entrée du menu à mettre en évidence pour la page affichée."""
    page = getattr(request, "resolver_match", None)
    if page is None:
        return ""
    if (page.url_name or "").startswith("plateforme"):
        return "plateforme"
    if page.url_name in ADMINISTRATION or page.namespace == "documents":
        return "administration"
    if page.namespace == "proprietes" and (page.url_name or "").startswith("bailleur"):
        return "contacts"
    if page.namespace == "candidatures":
        # Une candidature est une étape de la mise en location d'une propriété.
        return "proprietes"
    if page.namespace == "mandats":
        # Les mandats sont des contrats : ils se rangent sous cette rubrique.
        return "contrats"
    return page.namespace or ("accueil" if page.url_name == "accueil" else "")
