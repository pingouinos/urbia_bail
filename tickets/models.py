"""Tickets : les retours des utilisateurs sur l'application (bug, idée,
question). Ils restent dans l'application, chez l'exploitant, qui choisit ce
qui part dans le backlog GitHub : le dépôt est public, et un ticket peut
contenir le nom d'un locataire ou une capture d'écran."""

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import Truncator

from core.agence import RattacheAgence
from core.fichiers import nom_aleatoire


def dossier_capture(ticket, nom):
    return f"tickets/{nom_aleatoire(nom)}"


class Ticket(RattacheAgence):
    class Type(models.TextChoices):
        BUG = "bug", "Un bug"
        IDEE = "idee", "Une idée"
        QUESTION = "question", "Une question"

    class Etat(models.TextChoices):
        RECU = "recu", "Reçu"
        EN_COURS = "en_cours", "En cours"
        RESOLU = "resolu", "Résolu"

    type = models.CharField("c'est", max_length=10, choices=Type.choices, default=Type.BUG)
    texte = models.TextField("en une ou deux phrases")
    capture = models.FileField("capture d'écran", upload_to=dossier_capture, blank=True)
    # Contexte joint automatiquement : la page d'où le ticket est parti et l'appareil.
    page = models.CharField(max_length=300, blank=True)
    appareil = models.CharField(max_length=120, blank=True)
    auteur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    cree_le = models.DateTimeField("ouvert le", auto_now_add=True)

    etat = models.CharField("état", max_length=10, choices=Etat.choices, default=Etat.RECU)
    issue = models.URLField("issue GitHub", blank=True, help_text="Adresse de l'issue, une fois créée.")

    class Meta:
        ordering = ["-cree_le", "-pk"]
        verbose_name = "ticket"

    def __str__(self):
        return self.titre

    @property
    def titre(self):
        """Première ligne du texte, raccourcie."""
        return Truncator(self.texte.strip().splitlines()[0] if self.texte.strip() else "").chars(80)

    def get_absolute_url(self):
        return reverse("plateforme_ticket", args=[self.pk])
