"""Mandats de gestion confiés à l'agence par les bailleurs.

La loi Hoguet (art. 6 de la loi du 2 janvier 1970 et art. 65 du décret du
20 juillet 1972) impose un mandat écrit et un registre des mandats tenu sans
discontinuité : chaque mandat reçoit un numéro d'ordre définitif et n'est
jamais supprimé.
"""

from decimal import Decimal

from django.db import IntegrityError, models, transaction
from django.db.models import Max
from django.urls import reverse
from simple_history.models import HistoricalRecords

from biens.models import Bien, Horodatage

# Plafonds des honoraires de location imputables au locataire, en euros TTC
# par m² de surface habitable (décret n° 2014-890 du 1er août 2014).
PLAFOND_BAIL_ZONE_TENDUE = Decimal("10")
PLAFOND_BAIL_HORS_ZONE_TENDUE = Decimal("8")
PLAFOND_ETAT_DES_LIEUX = Decimal("3")

NOMBRES = {1: "un", 2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six"}


def _montant(**options):
    return models.DecimalField(max_digits=9, decimal_places=2, **options)


class Mandat(Horodatage):
    class BaseGestion(models.TextChoices):
        ENCAISSEMENTS = "encaissements", "Encaissements mensuels"
        HORS_CHARGES = "hors_charges", "Encaissements hors charges"
        CHARGES_COMPRISES = "charges_comprises", "Encaissements charges comprises"

    numero = models.PositiveIntegerField("numéro au registre", unique=True, editable=False)
    bien = models.ForeignKey(Bien, on_delete=models.PROTECT, related_name="mandats")

    # Parties, reprises de la fiche du bailleur et du bien puis figées : le
    # mandat signé ne change pas si la fiche évolue.
    mandant = models.CharField(max_length=255, help_text="Tel qu'il apparaîtra en tête du mandat.")
    mandant_adresse = models.CharField("adresse du mandant", max_length=255)
    mandant_code_postal_ville = models.CharField("code postal et ville du mandant", max_length=120)
    designation = models.TextField("désignation du bien", help_text="Nature, surface, composition.")
    adresse_bien = models.CharField("adresse du bien", max_length=255)
    loyer_cc = _montant(verbose_name="loyer charges comprises (€)")
    charges = _montant(verbose_name="dont charges (€)", default=0)

    # Durée
    date_signature = models.DateField("date de signature", null=True, blank=True)
    duree_ans = models.PositiveSmallIntegerField("durée initiale (ans)", default=3)
    reconductions = models.PositiveSmallIntegerField(
        "reconductions tacites", default=0, help_text="Nombre de reconductions prévues (0 si aucune)."
    )
    date_fin = models.DateField(
        "fin du mandat", null=True, blank=True, help_text="À renseigner à la résiliation ou à l'échéance."
    )

    # Rémunération
    taux_gestion = models.DecimalField(
        "honoraires de gestion (% TTC)", max_digits=5, decimal_places=2, default=Decimal("7")
    )
    base_gestion = models.CharField(
        "assiette des honoraires", max_length=20, choices=BaseGestion.choices,
        default=BaseGestion.CHARGES_COMPRISES,
    )
    assurance_loyers_impayes = models.BooleanField("assurance loyers impayés", default=False)
    taux_assurance = models.DecimalField(
        "taux de l'assurance (% TTC)", max_digits=5, decimal_places=2, default=Decimal("2.90")
    )
    honoraires_bail_bailleur = _montant(
        verbose_name="visite, dossier et bail : part bailleur (€ TTC)", default=0
    )
    honoraires_bail_locataire = _montant(
        verbose_name="visite, dossier et bail : part locataire (€ TTC)", default=0
    )
    honoraires_edl_bailleur = _montant(verbose_name="état des lieux : part bailleur (€ TTC)", default=0)
    honoraires_edl_locataire = _montant(verbose_name="état des lieux : part locataire (€ TTC)", default=0)

    fichier_signe = models.FileField(
        "mandat signé (scan)", upload_to="mandats/", blank=True,
    )
    observations = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-numero"]
        verbose_name = "mandat de gestion"
        verbose_name_plural = "mandats de gestion"

    def __str__(self):
        return f"Mandat n° {self.numero} · {self.mandant}"

    def get_absolute_url(self):
        return reverse("mandats:mandat", args=[self.pk])

    def save(self, *args, **kwargs):
        if self.numero:
            return super().save(*args, **kwargs)
        # Numéro suivant du registre ; on réessaie si un autre mandat a été
        # créé au même instant.
        for _ in range(5):
            self.numero = (Mandat.objects.aggregate(m=Max("numero"))["m"] or 0) + 1
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                if not Mandat.objects.filter(numero=self.numero).exists():
                    raise
        raise IntegrityError("Impossible d'attribuer un numéro de mandat.")

    @classmethod
    def depuis_bien(cls, bien):
        """Mandat prérempli à partir des fiches du bien et du bailleur."""
        bailleur = bien.bailleur
        loyer = (bien.dernier_loyer or 0) + (bien.charges or 0)
        return cls(
            bien=bien,
            mandant=bailleur.designation,
            mandant_adresse=bailleur.adresse,
            mandant_code_postal_ville=f"{bailleur.code_postal} {bailleur.ville}".strip(),
            designation=bien.designation,
            adresse_bien=f"{bien.adresse} {bien.code_postal} {bien.ville.upper()}",
            loyer_cc=loyer or None,
            charges=bien.charges or 0,
        )

    @property
    def en_cours(self):
        return self.date_fin is None

    @property
    def duree_texte(self):
        """« une durée de trois ans » ou « une durée d'un an, le contrat se
        renouvellera par tacite reconduction à trois reprises »."""
        nombre = NOMBRES.get(self.duree_ans, str(self.duree_ans))
        texte = "une durée d’un an" if self.duree_ans == 1 else f"une durée de {nombre} ans"
        if self.reconductions:
            reprises = NOMBRES.get(self.reconductions, str(self.reconductions))
            fois = "une reprise" if self.reconductions == 1 else f"{reprises} reprises"
            texte += f", le contrat se renouvellera par tacite reconduction à {fois}"
        return texte

    @property
    def honoraires_bail(self):
        return self.honoraires_bail_bailleur + self.honoraires_bail_locataire

    @property
    def honoraires_edl(self):
        return self.honoraires_edl_bailleur + self.honoraires_edl_locataire

    def plafonds_locataire(self):
        """Plafonds (bail, état des lieux) de la part locataire, ou None si
        le bien n'y est pas soumis ou si sa surface est inconnue."""
        if not self.bien.est_habitation or not self.bien.surface:
            return None
        par_m2 = PLAFOND_BAIL_ZONE_TENDUE if self.bien.zone_tendue else PLAFOND_BAIL_HORS_ZONE_TENDUE
        return self.bien.surface * par_m2, self.bien.surface * PLAFOND_ETAT_DES_LIEUX

    @property
    def alertes(self):
        """Points contraires à l'article 5 I de la loi du 6 juillet 1989 :
        la part du locataire ne peut excéder celle du bailleur ni le plafond
        par m² de surface habitable."""
        from documents.generation import montant

        if not self.bien.est_habitation:
            return []
        alertes = []
        prestations = [
            ("visite, dossier et bail", self.honoraires_bail_locataire, self.honoraires_bail_bailleur),
            ("état des lieux", self.honoraires_edl_locataire, self.honoraires_edl_bailleur),
        ]
        for libelle, locataire, bailleur in prestations:
            if locataire > bailleur:
                alertes.append(
                    f"Honoraires de {libelle} : la part du locataire ({montant(locataire)} €) dépasse "
                    f"celle du bailleur ({montant(bailleur)} €), ce que la loi interdit."
                )
        plafonds = self.plafonds_locataire()
        if plafonds is None:
            if self.honoraires_bail_locataire or self.honoraires_edl_locataire:
                alertes.append("Surface habitable du bien inconnue : plafonds non vérifiés.")
            return alertes
        for (libelle, locataire, _), plafond in zip(prestations, plafonds):
            if locataire > plafond:
                alertes.append(
                    f"Honoraires de {libelle} : la part du locataire ({montant(locataire)} €) dépasse "
                    f"le plafond légal de {montant(plafond)} € pour {montant(self.bien.surface)} m²."
                )
        return alertes

    def contexte_document(self):
        """Valeurs à placer dans le modèle Word du mandat."""
        from documents.generation import montant

        base = {
            self.BaseGestion.ENCAISSEMENTS: "",
            self.BaseGestion.HORS_CHARGES: " (hors charges)",
            self.BaseGestion.CHARGES_COMPRISES: " charges comprises",
        }[self.base_gestion]
        return {
            "numero": self.numero,
            "mandant": self.mandant,
            "mandant_adresse": self.mandant_adresse,
            "mandant_code_postal_ville": self.mandant_code_postal_ville,
            "designation": self.designation,
            "adresse_bien": self.adresse_bien,
            "loyer_cc": montant(self.loyer_cc),
            "charges": montant(self.charges),
            "duree": self.duree_texte,
            "assurance_loyers_impayes": self.assurance_loyers_impayes,
            "taux_assurance": montant(self.taux_assurance),
            "taux_gestion": montant(self.taux_gestion),
            "base_gestion": base,
            "honoraires_bail": montant(self.honoraires_bail),
            "honoraires_bail_bailleur": montant(self.honoraires_bail_bailleur),
            "honoraires_bail_locataire": montant(self.honoraires_bail_locataire),
            "honoraires_edl": montant(self.honoraires_edl),
            "honoraires_edl_bailleur": montant(self.honoraires_edl_bailleur),
            "honoraires_edl_locataire": montant(self.honoraires_edl_locataire),
            "date_signature": self.date_signature.strftime("%d/%m/%Y") if self.date_signature else "",
        }

    @property
    def nom_fichier(self):
        return f"Mandat {self.numero} - {self.mandant}"[:120]
