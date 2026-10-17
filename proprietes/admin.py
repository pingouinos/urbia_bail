from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Bailleur, Propriete


@admin.register(Bailleur)
class BailleurAdmin(SimpleHistoryAdmin):
    list_display = ["nom", "prenom", "type", "ville"]
    search_fields = ["nom", "prenom", "email"]


@admin.register(Propriete)
class ProprieteAdmin(SimpleHistoryAdmin):
    list_display = ["numero_lot", "adresse", "complement", "ville", "usage", "bailleur", "actif"]
    list_filter = ["usage", "actif", "classe_dpe", "zone_tendue"]
    search_fields = ["numero_lot", "adresse", "ville", "bailleur__nom"]
    autocomplete_fields = ["bailleur"]
