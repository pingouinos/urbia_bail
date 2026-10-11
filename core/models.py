"""L'agence qui utilise l'application : son identité, ses coordonnées et les
mentions légales que portent ses mandats et ses baux.

Une installation sert une seule agence, donc une seule fiche. Elle vit dans une
table et non dans la configuration pour qu'un administrateur la tienne à jour
sans toucher au serveur, et pour que le reste puisse s'y rattacher le jour où
plusieurs agences partageraient la même installation.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from proprietes.models import Horodatage


class Organisation(Horodatage):
    # Mentions sans lesquelles un mandat ou un bail n'est pas conforme.
    MENTIONS = ("nom", "adresse", "code_postal", "ville", "carte_pro", "carte_delivree_par", "garant")

    nom = models.CharField(
        "nom commercial", max_length=120, blank=True,
        help_text="Tel qu'il figure sur les documents et signe les messages aux candidats.",
    )
    raison_sociale = models.CharField("raison sociale", max_length=120, blank=True)
    forme_juridique = models.CharField("forme juridique", max_length=40, blank=True, help_text="SAS, SARL, EURL…")
    capital = models.PositiveIntegerField("capital social (€)", null=True, blank=True)
    siren = models.CharField("SIREN", max_length=20, blank=True)
    rcs_ville = models.CharField("ville du RCS", max_length=80, blank=True)
    logo = models.FileField(
        upload_to="organisation/", blank=True,
        help_text="Image PNG, JPEG ou WebP de 1 Mo au maximum. Remplace le logo de l'application à l'écran.",
    )

    adresse = models.CharField(max_length=200, blank=True)
    code_postal = models.CharField("code postal", max_length=10, blank=True)
    ville = models.CharField(max_length=80, blank=True)
    telephone = models.CharField("téléphone", max_length=30, blank=True)
    email = models.EmailField("e-mail", blank=True)
    site = models.URLField("site Internet", blank=True)

    carte_pro = models.CharField(
        "carte professionnelle", max_length=60, blank=True, help_text="Numéro complet, par exemple CPI 3101 2017 000 000 000.",
    )
    carte_delivree_par = models.CharField(
        "délivrée par", max_length=120, blank=True, help_text="Par exemple : la CCI de la Haute-Garonne.",
    )
    garant = models.CharField("garant financier", max_length=120, blank=True)
    garant_adresse = models.CharField("adresse du garant", max_length=200, blank=True)
    garantie_montant = models.PositiveIntegerField("montant de la garantie (€)", null=True, blank=True)

    taux_gestion = models.DecimalField(
        "honoraires de gestion proposés (% TTC)", max_digits=5, decimal_places=2, default=Decimal("7"),
        help_text="Taux prérempli à la création d'un mandat ; il reste modifiable sur chaque mandat.",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "agence"

    def __str__(self):
        return self.designation

    @classmethod
    def courante(cls):
        """La fiche de l'agence, créée vide à la première demande."""
        return cls.objects.order_by("pk").first() or cls.objects.create()

    @property
    def designation(self):
        return self.nom or settings.NOM_APPLICATION

    @property
    def manquants(self):
        """Libellés des mentions légales qui restent à renseigner."""
        return [str(self._meta.get_field(champ).verbose_name) for champ in self.MENTIONS if not getattr(self, champ)]

    def contexte_document(self):
        """Valeurs de l'agence pour les modèles Word, sous {{ agence.… }}."""
        from documents.generation import montant

        return {
            "nom": self.nom,
            "raison_sociale": self.raison_sociale,
            "forme_juridique": self.forme_juridique,
            "capital": montant(self.capital),
            "siren": self.siren,
            "rcs_ville": self.rcs_ville,
            "adresse": self.adresse,
            "code_postal": self.code_postal,
            "ville": self.ville,
            "telephone": self.telephone,
            "email": self.email,
            "site": self.site,
            "carte_pro": self.carte_pro,
            "carte_delivree_par": self.carte_delivree_par,
            "garant": self.garant,
            "garant_adresse": self.garant_adresse,
            "garantie_montant": montant(self.garantie_montant),
        }
