from django.conf import settings


def application(request):
    return {"nom_application": settings.NOM_APPLICATION, "rubrique": rubrique(request)}


def rubrique(request):
    """Entrée du menu à mettre en évidence pour la page affichée."""
    page = getattr(request, "resolver_match", None)
    if page is None:
        return ""
    if page.namespace == "biens" and (page.url_name or "").startswith("bailleur"):
        return "bailleurs"
    return page.namespace
