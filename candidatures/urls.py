from django.urls import path

from . import views

app_name = "candidatures"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("nouvelle/", views.candidature_editer, name="creer"),
    path("<int:pk>/", views.candidature, name="candidature"),
    path("<int:pk>/modifier/", views.candidature_editer, name="modifier"),
    path("<int:pk>/decision/", views.decider, name="decider"),
    path("<int:pk>/reponse/", views.reponse, name="reponse"),
    path("<int:pk>/effacer/", views.effacer, name="effacer"),
]
