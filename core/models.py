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

from core.champs import TelephoneField, code_postal_francais, siren_valide
from core.fichiers import nom_aleatoire

from proprietes.models import Horodatage


def ranger_logo(organisation, nom):
    return f"organisation/{nom_aleatoire(nom)}"


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
    siren = models.CharField("SIREN", max_length=20, blank=True, validators=[siren_valide])
    rcs_ville = models.CharField("ville du RCS", max_length=80, blank=True)
    logo = models.FileField(
        upload_to=ranger_logo, blank=True,
        help_text="Image de 1 Mo au maximum. Elle remplace le logo de l'application à l'écran et, si elle est "
                  "en PNG ou en JPEG, celui de l'en-tête des mandats et des baux.",
    )

    adresse = models.CharField(max_length=200, blank=True)
    code_postal = models.CharField("code postal", max_length=10, blank=True, validators=[code_postal_francais])
    ville = models.CharField(max_length=80, blank=True)
    telephone = TelephoneField("téléphone", blank=True)
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


class Evenement(models.Model):
    """Ligne du journal de sécurité (voir core/journal.py)."""

    CONSERVATION_JOURS = 365

    date = models.DateTimeField(auto_now_add=True, db_index=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    # Gardé en clair : il reste lisible si le compte est supprimé, et sert aux connexions refusées.
    identifiant = models.CharField(max_length=150, blank=True)
    ip = models.CharField("adresse IP", max_length=45, blank=True)
    action = models.CharField(max_length=40, db_index=True)
    objet = models.CharField(max_length=200, blank=True)
    detail = models.CharField("détail", max_length=300, blank=True)

    class Meta:
        ordering = ["-date", "-pk"]
        verbose_name = "événement"

    def __str__(self):
        return f"{self.date:%d/%m/%Y %H:%M} · {self.action} · {self.identifiant}"
