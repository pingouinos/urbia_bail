from django.contrib import admin

from .models import DemandeSignature, Signataire


class SignataireEnLigne(admin.TabularInline):
    model = Signataire
    extra = 0
    readonly_fields = ["ordre", "role", "qualite", "nom", "email", "identifiant", "statut", "signe_le", "relance_le"]
    can_delete = False


@admin.register(DemandeSignature)
class DemandeSignatureAdmin(admin.ModelAdmin):
    list_display = ["__str__", "statut", "envoyee_le", "terminee_le"]
    list_filter = ["statut"]
    readonly_fields = ["bail", "mandat", "soumission", "statut", "envoyee_le", "envoyee_par", "terminee_le",
                       "synchronisee_le"]
    inlines = [SignataireEnLigne]
