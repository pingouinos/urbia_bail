from django.urls import path

from . import views

app_name = "baux"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("nouveau/", views.bail_editer, name="creer"),
    path("<int:pk>/", views.bail, name="bail"),
    path("<int:pk>/modifier/", views.bail_editer, name="modifier"),
    path("<int:pk>/bail.docx", views.telecharger, {"format_": "docx"}, name="word"),
    path("<int:pk>/bail.pdf", views.telecharger, {"format_": "pdf"}, name="pdf"),
    path("<int:pk>/signe/", views.deposer_signe, name="deposer_signe"),
    path("<int:pk>/signe/fichier", views.fichier_signe, name="fichier_signe"),
]
