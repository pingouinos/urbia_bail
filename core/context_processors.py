from django.conf import settings


def application(request):
    return {"nom_application": settings.NOM_APPLICATION}
