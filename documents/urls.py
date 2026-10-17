from django.urls import path

from . import views, archives_views

app_name = "documents"

urlpatterns = [
    path("archives/<int:pk>/<str:format_>/", archives_views.telecharger, name="archive"),
    path("contrat/<str:type_>/<int:pk>/preparer/", archives_views.preparer, name="preparer"),
    path("", views.modeles, name="modeles"),
    path("regles/", views.regles, name="regles"),
    path("<str:nom>/", views.modele, name="modele"),
    path("<str:nom>/texte/", views.texte, name="texte"),
    path("<str:nom>/essai/version/", views.essai_version, name="essai_version"),
    path("<str:nom>/mettre-en-service/", views.mettre_en_service_essai, name="mettre_en_service"),
    path("<str:nom>/abandonner/", views.abandonner, name="abandonner"),
    path("<str:nom>/telecharger/", views.telecharger, name="telecharger"),
    path("<str:nom>/essai/", views.essai, name="essai"),
    path("<str:nom>/reprendre/", views.reprendre, name="reprendre"),
    path("<str:nom>/deposer/", views.deposer, name="deposer"),
    path("<str:nom>/precedent/", views.precedent, name="precedent"),
    path("<str:nom>/retablir/", views.retablir, name="retablir"),
    path("<str:nom>/relu/", views.relu, name="relu"),
    path("<str:nom>/corriger/", views.corriger, name="corriger"),
    path("<str:nom>/corriger/essai/", views.essai_correction, name="essai_correction"),
    path("<str:nom>/corriger/appliquer/", views.appliquer_correction, name="appliquer_correction"),
]
