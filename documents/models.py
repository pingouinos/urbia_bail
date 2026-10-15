from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Q


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


class VersionContrat(models.Model):
    """Archives ajoutées, jamais réécrites. Les octets vivent dans la sauvegarde DB."""

    bail = models.ForeignKey("contrats.Bail", null=True, on_delete=models.PROTECT, related_name="versions_document")
    mandat = models.ForeignKey("mandats.Mandat", null=True, on_delete=models.PROTECT, related_name="versions_document")
    nature = models.CharField(max_length=20, choices=[
        ("preparation", "Document préparé pour signature"),
        ("signature", "Signature enregistrée"),
        ("correction", "Correction administrative — non signée"),
        ("correction_fin", "Correction de fin — document conservé"),
        ("reprise", "Reconstitution actuelle — original historique inconnu"),
        ("scan", "Exemplaire signé déposé"),
    ])
    source = models.ForeignKey("self", null=True, on_delete=models.PROTECT)
    cree_le = models.DateTimeField(auto_now_add=True)
    auteur = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    auteur_nom = models.CharField(max_length=150)
    motif = models.TextField()
    contexte = models.JSONField()
    modele = models.BinaryField()
    docx = models.BinaryField()
    empreinte = models.CharField(max_length=64)
    date_signature = models.DateField(null=True)
    scan = models.BinaryField(null=True)
    scan_nom = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-pk"]
        constraints = [models.CheckConstraint(
            condition=(Q(bail__isnull=False, mandat__isnull=True) | Q(bail__isnull=True, mandat__isnull=False)),
            name="archive_un_seul_contrat",
        )]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Une archive ne se modifie pas : créer une nouvelle version.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Une archive contractuelle doit être conservée.")


class PdfArchive(models.Model):
    version = models.OneToOneField(VersionContrat, on_delete=models.PROTECT, related_name="pdf_archive")
    contenu = models.BinaryField()
