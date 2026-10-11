from django.urls import path

from . import views

app_name = "contacts"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("nouveau/", views.contact_editer, name="nouveau"),
    path("<int:pk>/", views.contact, name="contact"),
    path("<int:pk>/modifier/", views.contact_editer, name="modifier"),
]
