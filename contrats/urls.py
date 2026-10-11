from django.urls import path

from . import views

app_name = "contrats"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("ajouter/", views.choisir, name="choisir"),
    path("nouveau/", views.bail_editer, name="creer"),
    path("nouveau/logement/", views.nouveau_logement, name="nouveau_logement"),
    path("<int:pk>/", views.bail, name="bail"),
    path("<int:pk>/modifier/", views.bail_editer, name="modifier"),
    path("<int:pk>/fin/", views.fin, name="fin"),
    path("<int:pk>/supprimer/", views.supprimer, name="supprimer"),
    path("<int:pk>/loyers/", views.loyers_suivre, name="loyers_suivre"),
    path("echeances/<int:pk>/pointer/", views.echeance_pointer, name="echeance_pointer"),
    path("echeances/<int:pk>/avis/", views.echeance_document, {"type_": "avis"}, name="echeance_avis"),
    path("echeances/<int:pk>/quittance/", views.echeance_document, {"type_": "quittance"}, name="echeance_quittance"),
    path("<int:pk>/bail.docx", views.telecharger, {"format_": "docx"}, name="word"),
    path("<int:pk>/bail.pdf", views.telecharger, {"format_": "pdf"}, name="pdf"),
    path("<int:pk>/signe/", views.deposer_signe, name="deposer_signe"),
    path("<int:pk>/signe/fichier", views.fichier_signe, name="fichier_signe"),
]
