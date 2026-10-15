from django.urls import path

from . import views, archives_views

app_name = "documents"

urlpatterns = [
    path("archives/<int:pk>/<str:format_>/", archives_views.telecharger, name="archive"),
    path("contrat/<str:type_>/<int:pk>/preparer/", archives_views.preparer, name="preparer"),
    path("", views.modeles, name="modeles"),
    path("<str:nom>/telecharger/", views.telecharger, name="telecharger"),
    path("<str:nom>/essai/", views.essai, name="essai"),
    path("<str:nom>/reprendre/", views.reprendre, name="reprendre"),
    path("<str:nom>/deposer/", views.deposer, name="deposer"),
    path("<str:nom>/precedent/", views.precedent, name="precedent"),
    path("<str:nom>/retablir/", views.retablir, name="retablir"),
]
