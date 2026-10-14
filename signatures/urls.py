from django.urls import path

from . import views

app_name = "signatures"

urlpatterns = [
    path("<str:type_>/<int:pk>/envoyer/", views.envoyer, name="envoyer"),
    path("signataire/<int:pk>/relancer/", views.relancer, name="relancer"),
    path("demande/<int:pk>/annuler/", views.annuler, name="annuler"),
    path("docuseal/<str:jeton>/", views.retour_docuseal, name="retour_docuseal"),
]
