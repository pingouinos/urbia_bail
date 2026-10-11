from django.conf import settings


def application(request):
    return {"nom_application": settings.NOM_APPLICATION, "rubrique": rubrique(request)}


def rubrique(request):
    """Entrée du menu à mettre en évidence pour la page affichée."""
    page = getattr(request, "resolver_match", None)
    if page is None:
        return ""
    if page.namespace == "proprietes" and (page.url_name or "").startswith("bailleur"):
        return "bailleurs"
    if page.namespace == "mandats":
        # Les mandats sont des contrats : ils se rangent sous cette rubrique.
        return "contrats"
    return page.namespace or ("accueil" if page.url_name == "accueil" else "")
