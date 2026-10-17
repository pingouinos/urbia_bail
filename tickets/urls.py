from django.urls import path

from . import views

app_name = "tickets"

urlpatterns = [
    path("", views.mes_tickets, name="liste"),
    path("<int:pk>/capture/", views.capture, name="capture"),
]
