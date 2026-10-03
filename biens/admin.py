from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Bailleur, Bien


@admin.register(Bailleur)
class BailleurAdmin(SimpleHistoryAdmin):
    list_display = ["nom", "prenom", "type", "ref_ics", "ville"]
    search_fields = ["nom", "prenom", "ref_ics"]


@admin.register(Bien)
class BienAdmin(SimpleHistoryAdmin):
    list_display = ["ref_ics", "adresse", "complement", "ville", "usage", "bailleur", "actif"]
    list_filter = ["usage", "actif", "classe_dpe", "zone_tendue"]
    search_fields = ["ref_ics", "adresse", "ville", "bailleur__nom"]
    autocomplete_fields = ["bailleur"]
