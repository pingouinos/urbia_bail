from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "contrats"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("ajouter/", views.choisir, name="choisir"),
    path("nouveau/", views.bail_editer, name="creer"),
    path("nouveau/logement/", views.nouveau_logement, name="nouveau_logement"),
    path("<int:pk>/", views.etapes, name="bail"),
    # Ancienne adresse de la page des étapes, gardée pour les liens déjà notés.
    path("<int:pk>/modifier/", RedirectView.as_view(pattern_name="contrats:bail", query_string=True), name="modifier"),
    path("<int:pk>/signature/retirer/", views.retirer_signature, name="retirer_signature"),
    path("<int:pk>/conge/annuler/", views.annuler_conge, name="annuler_conge"),
    path("<int:pk>/fin/", views.fin, name="fin"),
    path("<int:pk>/entree/", views.entree, name="entree"),
    path("<int:pk>/sortie/", views.edl_sortie, name="edl_sortie"),
    path("<int:pk>/depot/", views.restitution, name="restitution"),
    path("<int:pk>/fichiers/<str:champ>", views.fichier_edl, name="fichier_edl"),
    path("<int:pk>/revision/", views.reviser, name="reviser"),
    path("<int:pk>/assurance/", views.assurance, name="assurance"),
    path("<int:pk>/supprimer/", views.supprimer, name="supprimer"),
    path("<int:pk>/bail.pdf", views.telecharger, {"format_": "pdf"}, name="pdf"),
    path("<int:pk>/signe/", views.deposer_signe, name="deposer_signe"),
    path("<int:pk>/signe/fichier", views.fichier_signe, name="fichier_signe"),
]
