from django.conf import settings
from django.db import models


class Modele(models.Model):
    """État d'un modèle Word : d'où vient la version en service, et le Google
    Doc dont l'agence le reprend."""

    class Origine(models.TextChoices):
        ORIGINE = "origine", "Modèle d'origine"
        DEPOT = "depot", "Fichier Word déposé"
        GOOGLE = "google", "Repris de Google Docs"
        PRECEDENT = "precedent", "Version précédente remise en service"

    nom = models.CharField(max_length=60, unique=True)
    lien_google = models.URLField("lien du Google Doc", max_length=300, blank=True)
    origine = models.CharField(max_length=10, choices=Origine.choices, default=Origine.ORIGINE)
    mis_en_service_le = models.DateTimeField("mis en service le", null=True, blank=True)
    mis_en_service_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="mis en service par",
    )

    class Meta:
        verbose_name = "modèle"

    def __str__(self):
        return self.nom
