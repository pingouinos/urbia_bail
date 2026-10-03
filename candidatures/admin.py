from django.contrib import admin

from .models import Candidat, Candidature


class CandidatInline(admin.TabularInline):
    model = Candidat
    extra = 0


@admin.register(Candidature)
class CandidatureAdmin(admin.ModelAdmin):
    list_display = ["__str__", "statut", "cree_le", "decide_le"]
    list_filter = ["statut"]
    search_fields = ["bien__adresse", "candidats__nom"]
    inlines = [CandidatInline]
