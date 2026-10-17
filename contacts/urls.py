from django.urls import path

from . import views

app_name = "contacts"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("nouveau/", views.contact_editer, name="nouveau"),
    path("import/", views.importer, name="importer"),
    path("import/confirmer/", views.importer_confirmer, name="importer_confirmer"),
    path("modele.xlsx", views.modele, name="modele"),
    path("<int:pk>/", views.contact, name="contact"),
    path("<int:pk>/modifier/", views.contact_editer, name="modifier"),
    path("<int:pk>/supprimer/", views.supprimer, name="supprimer"),
]
