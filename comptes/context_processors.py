from django.conf import settings


def connexion(request):
    return {"google_active": settings.GOOGLE_CONNEXION_ACTIVE}
