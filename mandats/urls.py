from django.urls import path

from . import views

app_name = "mandats"

urlpatterns = [
    path("", views.registre, name="registre"),
    path("registre.xlsx", views.registre_export, name="registre_export"),
    path("nouveau/", views.mandat_editer, name="creer"),
    path("<int:pk>/", views.mandat, name="mandat"),
    path("<int:pk>/modifier/", views.mandat_editer, name="modifier"),
    path("<int:pk>/mandat.docx", views.telecharger, {"format_": "docx"}, name="word"),
    path("<int:pk>/mandat.pdf", views.telecharger, {"format_": "pdf"}, name="pdf"),
    path("<int:pk>/signe/", views.deposer_signe, name="deposer_signe"),
    path("<int:pk>/signe/fichier", views.fichier_signe, name="fichier_signe"),
]
