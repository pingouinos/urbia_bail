from django.urls import path

from . import views

app_name = "proprietes"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("nouveau/", views.propriete_editer, name="propriete_creer"),
    path("<int:pk>/", views.propriete, name="propriete"),
    path("<int:pk>/modifier/", views.propriete_editer, name="propriete_modifier"),
    path("<int:pk>/supprimer/", views.propriete_supprimer, name="propriete_supprimer"),
    path("<int:pk>/mise-en-location/", views.mettre_en_location, name="mettre_en_location"),
    path("<int:pk>/documents/", views.piece_deposer, name="piece_deposer"),
    path("bailleurs/<int:pk>/documents/", views.bailleur_piece_deposer, name="bailleur_piece_deposer"),
    path("documents/<int:pk>/", views.piece, name="piece"),
    path("documents/<int:pk>/supprimer/", views.piece_supprimer, name="piece_supprimer"),
    path("immeubles/nouveau/", views.immeuble_creer, name="immeuble_creer"),
    path("immeubles/<int:pk>/", views.immeuble, name="immeuble"),
    path("immeubles/<int:pk>/modifier/", views.immeuble_editer, name="immeuble_modifier"),
    path("immeubles/<int:pk>/supprimer/", views.immeuble_supprimer, name="immeuble_supprimer"),
    path("bailleurs/", views.bailleurs, name="bailleurs"),
    path("bailleurs/nouveau/", views.bailleur_editer, name="bailleur_creer"),
    path("bailleurs/<int:pk>/", views.bailleur, name="bailleur"),
    path("bailleurs/<int:pk>/modifier/", views.bailleur_editer, name="bailleur_modifier"),
    path("bailleurs/<int:pk>/supprimer/", views.bailleur_supprimer, name="bailleur_supprimer"),
    path("import/", views.importer, name="importer"),
    path("import/confirmer/", views.importer_confirmer, name="importer_confirmer"),
    path("modele.xlsx", views.modele, name="modele"),
    path("export.xlsx", views.exporter, name="exporter"),
]
