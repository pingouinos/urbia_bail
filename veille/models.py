from django.conf import settings
from django.db import models


class Source(models.Model):
    cle = models.CharField(max_length=60, unique=True)
    dernier_essai = models.DateTimeField(null=True, blank=True)
    dernier_succes = models.DateTimeField(null=True, blank=True)
    erreur = models.CharField(max_length=500, blank=True)

    def __str__(self):
        from .catalogue import SOURCES

        reference = SOURCES.get(self.cle)
        return reference.nom if reference else self.cle


class Instantane(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="instantanes")
    cree_le = models.DateTimeField(auto_now_add=True)
    url = models.URLField(max_length=500)
    texte = models.TextField()
    empreinte = models.CharField(max_length=64)

    class Meta:
        ordering = ["-pk"]


class Alerte(models.Model):
    class Statut(models.TextChoices):
        A_ANALYSER = "a_analyser", "À analyser"
        CONFIRME = "confirme", "Impact confirmé — action à prévoir"
        SANS_IMPACT = "sans_impact", "Sans impact"
        TRAITE = "traite", "Traitée"

    avant = models.ForeignKey(Instantane, on_delete=models.PROTECT, related_name="alertes_avant")
    apres = models.OneToOneField(Instantane, on_delete=models.PROTECT, related_name="alerte")
    differences = models.TextField()
    modeles_potentiels = models.JSONField(default=list)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.A_ANALYSER)

    class Meta:
        ordering = ["-pk"]


class Decision(models.Model):
    """L'examen d'une alerte ne vaut pas validation d'un modèle de contrat."""
    alerte = models.ForeignKey(Alerte, on_delete=models.PROTECT, related_name="decisions")
    cree_le = models.DateTimeField(auto_now_add=True)
    auteur = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    auteur_nom = models.CharField(max_length=150)
    statut = models.CharField(max_length=20, choices=Alerte.Statut.choices)
    commentaire = models.TextField()

    class Meta:
        ordering = ["-pk"]
