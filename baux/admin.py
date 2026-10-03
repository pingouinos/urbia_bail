from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Bail, Locataire


class LocataireInline(admin.TabularInline):
    model = Locataire
    extra = 0


@admin.register(Bail)
class BailAdmin(SimpleHistoryAdmin):
    list_display = ["bien", "type", "date_effet", "loyer", "date_fin_effective"]
    list_filter = ["type"]
    search_fields = ["bien__adresse", "locataires__nom"]
    inlines = [LocataireInline]
