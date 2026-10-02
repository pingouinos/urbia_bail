from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from core import views

admin.site.site_header = "UrbiaBail · administration"
admin.site.site_title = "UrbiaBail"

urlpatterns = [
    path("", views.accueil, name="accueil"),
    path("sante/", views.sante, name="sante"),
    path(
        "connexion/",
        auth_views.LoginView.as_view(redirect_authenticated_user=True),
        name="connexion",
    ),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="deconnexion"),
    path("admin/", admin.site.urls),
]
