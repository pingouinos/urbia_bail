from django.urls import path

from . import views

app_name = "biens"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("nouveau/", views.bien_editer, name="bien_creer"),
    path("<int:pk>/", views.bien, name="bien"),
    path("<int:pk>/modifier/", views.bien_editer, name="bien_modifier"),
    path("bailleurs/", views.bailleurs, name="bailleurs"),
    path("bailleurs/nouveau/", views.bailleur_editer, name="bailleur_creer"),
    path("bailleurs/<int:pk>/", views.bailleur, name="bailleur"),
    path("bailleurs/<int:pk>/modifier/", views.bailleur_editer, name="bailleur_modifier"),
    path("import/", views.importer, name="importer"),
    path("import/confirmer/", views.importer_confirmer, name="importer_confirmer"),
    path("modele.xlsx", views.modele, name="modele"),
    path("export.xlsx", views.exporter, name="exporter"),
]
