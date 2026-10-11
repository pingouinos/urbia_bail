from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from candidatures import views as candidatures_views
from comptes import views as comptes_views
from core import views

admin.site.site_header = f"{settings.NOM_APPLICATION} · administration"
admin.site.site_title = settings.NOM_APPLICATION

urlpatterns = [
    path("", views.accueil, name="accueil"),
    path("sante/", views.sante, name="sante"),
    path("administration/", views.administration, name="administration"),
    path("administration/agence/", views.agence, name="agence"),
    path("administration/journal/", views.journal, name="journal"),
    path("administration/utilisateurs/", views.utilisateurs, name="utilisateurs"),
    path("administration/utilisateurs/nouveau/", views.utilisateur_editer, name="utilisateur_creer"),
    path("administration/utilisateurs/<int:pk>/", views.utilisateur_editer, name="utilisateur_modifier"),
    path("administration/utilisateurs/<int:pk>/mfa/", views.utilisateur_action, {"action": "mfa"}, name="utilisateur_mfa"),
    path("administration/utilisateurs/<int:pk>/debloquer/", views.utilisateur_action, {"action": "debloquer"},
         name="utilisateur_debloquer"),
    path("agence/", RedirectView.as_view(pattern_name="agence")),
    path("logo-agence/", views.logo_agence, name="logo_agence"),
    path("proprietes/", include("proprietes.urls")),
    path("mandats/", include("mandats.urls")),
    path("candidatures/", include("candidatures.urls")),
    path("contrats/", include("contrats.urls")),
    path("contacts/", include("contacts.urls")),
    path("modeles/", include("documents.urls")),
    # Anciennes adresses, pour les favoris et les liens déjà envoyés.
    re_path(r"^biens/(?P<suite>.*)$", RedirectView.as_view(url="/proprietes/%(suite)s", query_string=True)),
    re_path(r"^baux/(?P<suite>.*)$", RedirectView.as_view(url="/contrats/%(suite)s", query_string=True)),
    # Formulaire du candidat, seule partie ouverte sans connexion.
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
