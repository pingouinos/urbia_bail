from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from candidatures import views as candidatures_views
from comptes import views as comptes_views
from core import views

admin.site.site_header = f"{settings.NOM_APPLICATION} · administration"
admin.site.site_title = settings.NOM_APPLICATION

urlpatterns = [
    path("", views.accueil, name="accueil"),
    path("sante/", views.sante, name="sante"),
    path("biens/", include("biens.urls")),
    path("mandats/", include("mandats.urls")),
    path("candidatures/", include("candidatures.urls")),
    path("baux/", include("baux.urls")),
    path("modeles/", include("documents.urls")),
    # Formulaire du candidat retenu, seule partie ouverte sans connexion.
    path("locataire/merci/", candidatures_views.formulaire_locataire_merci, name="formulaire_locataire_merci"),
    path("locataire/<str:jeton>/", candidatures_views.formulaire_locataire, name="formulaire_locataire"),
    path(
        "connexion/",
        auth_views.LoginView.as_view(redirect_authenticated_user=True),
        name="connexion",
    ),
    path("oidc/", include("mozilla_django_oidc.urls")),
    path("mfa/", comptes_views.mfa, name="mfa"),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="deconnexion"),
    path("admin/", admin.site.urls),
]
