from django.urls import path

from . import views

app_name = "veille"
urlpatterns = [
    path("", views.tableau, name="tableau"),
    path("<int:pk>/", views.detail, name="detail"),
]
