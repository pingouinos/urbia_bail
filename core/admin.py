from django.contrib import admin

from .models import Membre, Organisation


class LectureSeule:
    """Consultation seulement : les agences et leurs comptes se créent et se
    modifient dans Plateforme, qui pose le rôle d'administrateur et note
    chaque changement au journal."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MembreEnLigne(LectureSeule, admin.TabularInline):
    model = Membre
    fields = ["utilisateur"]
    extra = 0


@admin.register(Organisation)
class OrganisationAdmin(LectureSeule, admin.ModelAdmin):
    list_display = ["designation", "ville", "carte_pro", "active", "cree_le"]
    list_filter = ["active"]
    search_fields = ["nom", "raison_sociale", "ville", "siren"]
    inlines = [MembreEnLigne]


@admin.register(Membre)
class MembreAdmin(LectureSeule, admin.ModelAdmin):
    list_display = ["utilisateur", "organisation"]
    list_filter = ["organisation"]
    search_fields = ["utilisateur__username", "utilisateur__email", "organisation__nom"]
