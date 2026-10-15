from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from documents.admin_contracts import ContratArchiveAdminMixin

from .models import Mandat


@admin.register(Mandat)
class MandatAdmin(ContratArchiveAdminMixin, SimpleHistoryAdmin):
    list_display = ["numero", "mandant", "adresse_bien", "date_signature", "date_fin"]
    search_fields = ["mandant", "adresse_bien"]
    readonly_fields = ["numero"]

    def has_delete_permission(self, request, obj=None):
        # Le registre des mandats ne comporte ni suppression ni trou.
        return False
