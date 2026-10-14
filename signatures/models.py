"""Demandes de signature électronique d'un bail ou d'un mandat, et où en est
chaque signataire. La demande vit dans DocuSeal ; ces fiches en gardent les
numéros et le dernier état connu, mis à jour par le retour de DocuSeal
(webhook) ou par la commande synchroniser_signatures."""

from django.conf import settings
from django.db import models
from django.db.models import Q


class DemandeSignature(models.Model):
    class Statut(models.TextChoices):
        ENVOYEE = "envoyee", "Chez les signataires"
        SIGNEE = "signee", "Signée"
        REFUSEE = "refusee", "Refusée"
        ANNULEE = "annulee", "Annulée"

    bail = models.ForeignKey(
        "contrats.Bail", on_delete=models.CASCADE, null=True, blank=True, related_name="demandes_signature",
    )
    mandat = models.ForeignKey(
        "mandats.Mandat", on_delete=models.CASCADE, null=True, blank=True, related_name="demandes_signature",
    )
    soumission = models.PositiveBigIntegerField("numéro DocuSeal", unique=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.ENVOYEE)
    envoyee_le = models.DateTimeField("envoyée le", auto_now_add=True)
    envoyee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    terminee_le = models.DateTimeField("terminée le", null=True, blank=True)
    synchronisee_le = models.DateTimeField("dernière mise à jour", null=True, blank=True)

    class Meta:
        ordering = ["-envoyee_le"]
        verbose_name = "demande de signature"
        verbose_name_plural = "demandes de signature"
        constraints = [
            models.CheckConstraint(
                condition=Q(bail__isnull=False, mandat__isnull=True) | Q(bail__isnull=True, mandat__isnull=False),
                name="demande_signature_un_contrat",
            ),
        ]

    def __str__(self):
        return f"Signature · {self.contrat}"

    @property
    def contrat(self):
        return self.bail or self.mandat

    @property
    def en_cours(self):
        return self.statut == self.Statut.ENVOYEE


class Signataire(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "attente", "Pas encore ouvert"
        OUVERT = "ouvert", "Ouvert, pas signé"
        SIGNE = "signe", "Signé"
        REFUSE = "refuse", "Refusé"

    demande = models.ForeignKey(DemandeSignature, on_delete=models.CASCADE, related_name="signataires")
    ordre = models.PositiveSmallIntegerField()
    #: Rôle dans DocuSeal, repris des balises du document (« Locataire 1 », « Agence »…).
    role = models.CharField(max_length=40)
    #: À quel titre la personne signe, pour l'affichage (« locataire », « pour le bailleur »…).
    qualite = models.CharField("qualité", max_length=120)
    nom = models.CharField(max_length=200)
    email = models.EmailField("e-mail")
    identifiant = models.PositiveBigIntegerField("numéro DocuSeal", null=True, blank=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.EN_ATTENTE)
    signe_le = models.DateTimeField("signé le", null=True, blank=True)
    relance_le = models.DateTimeField("relancé le", null=True, blank=True)

    class Meta:
        ordering = ["demande", "ordre"]
        verbose_name = "signataire"

    def __str__(self):
        return f"{self.nom} · {self.qualite}"
