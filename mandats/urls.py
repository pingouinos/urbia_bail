from django.urls import path
from django.views.generic import RedirectView

from . import proprietaire, views

app_name = "mandats"

urlpatterns = [
    path("", views.registre, name="registre"),
    path("registre.xlsx", views.registre_export, name="registre_export"),
    path("nouveau/", views.mandat_editer, name="creer"),
    path("<int:pk>/", views.etapes, name="mandat"),
    # Ancienne adresse de la page des étapes, gardée pour les liens déjà notés.
    path("<int:pk>/modifier/", RedirectView.as_view(pattern_name="mandats:mandat", query_string=True), name="modifier"),
    path("<int:pk>/signature/retirer/", views.retirer_signature, name="retirer_signature"),
    path("<int:pk>/fin/", views.fin, name="fin"),
    path("<int:pk>/reconduction/", views.information_reconduction, name="information_reconduction"),
    path("<int:pk>/mandat.pdf", views.telecharger, {"format_": "pdf"}, name="pdf"),
    path("<int:pk>/signe/", views.deposer_signe, name="deposer_signe"),
    path("<int:pk>/lien/", proprietaire.lien, name="lien"),
    path("<int:pk>/signe/fichier", views.fichier_signe, name="fichier_signe"),
]
