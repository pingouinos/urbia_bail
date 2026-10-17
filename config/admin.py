"""Console Django (/admin/), réservée au superutilisateur.

Elle montre les données de toutes les agences sans filtre : un administrateur
d'agence, qui a pourtant le statut « staff », gère son agence par les écrans
de l'application et n'y entre pas.
"""

from django.contrib import admin
from django.contrib.admin.apps import AdminConfig


class Console(admin.AdminSite):
    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser


class ConsoleConfig(AdminConfig):
    default_site = "config.admin.Console"
