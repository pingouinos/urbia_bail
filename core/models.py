"""Les agences qui utilisent l'application : leur identité, leurs coordonnées
et les mentions légales que portent leurs mandats et leurs baux.

Une installation peut servir plusieurs agences. Chaque compte appartient à une
agence (`Membre`), et le code lit celle du compte connecté par
`Organisation.courante()`. Les biens, baux et autres données s'y rattacheront
à leur tour ; d'ici là, une installation ne sert en pratique qu'une agence.
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
    MENTIONS = (
        "nom", "adresse", "code_postal", "ville", "carte_pro", "carte_delivree_par", "garant", "garantie_montant",
        "rcp", "mediateur", "mediateur_site",
    )
    # Exigées seulement quand l'agence propose l'assurance loyers impayés.
    MENTIONS_ASSURANCE = ("orias", "assurance_gli", "assurance_gli_contrat")

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
    rcp = models.CharField(
        "assurance responsabilité civile professionnelle", max_length=200, blank=True,
        help_text="Assureur et numéro de contrat, par exemple : MMA, contrat n° 123456 (loi Hoguet, article 3).",
    )
    mediateur = models.CharField(
        "médiateur de la consommation", max_length=200, blank=True,
        help_text="Nom et adresse du médiateur auquel l'agence adhère (code de la consommation, L612-1).",
    )
    mediateur_site = models.URLField("site du médiateur", blank=True)
    orias = models.CharField(
        "numéro ORIAS", max_length=20, blank=True,
        help_text="Obligatoire pour proposer l'assurance loyers impayés aux propriétaires.",
    )
    assurance_gli = models.CharField(
        "assureur loyers impayés", max_length=120, blank=True, help_text="Par exemple : SADA Assurances.",
    )
    assurance_gli_contrat = models.CharField("numéro du contrat loyers impayés", max_length=60, blank=True)

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
        """L'agence du compte connecté (ou celle qu'une commande a activée).
        Hors de ce contexte (lien du candidat, commandes, tests), la première
        agence, créée vide à la première demande."""
        from .agence import agence_active

        return agence_active() or cls.premiere()

    @classmethod
    def premiere(cls):
        return cls.objects.order_by("pk").first() or cls.objects.create()

    @property
    def designation(self):
        return self.nom or settings.NOM_APPLICATION

    @property
    def manquants(self):
        """Libellés des mentions légales qui restent à renseigner."""
        return self._libelles_vides(self.MENTIONS)

    @property
    def manquants_assurance(self):
        return self._libelles_vides(self.MENTIONS_ASSURANCE)

    def _libelles_vides(self, champs):
        return [str(self._meta.get_field(champ).verbose_name) for champ in champs if not getattr(self, champ)]

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
            "rcp": self.rcp,
            "mediateur": self.mediateur,
            "mediateur_site": self.mediateur_site,
            "orias": self.orias,
            "assurance_gli": self.assurance_gli,
            "assurance_gli_contrat": self.assurance_gli_contrat,
        }


class Membre(models.Model):
    """Appartenance d'un compte à son agence. Une fiche à part plutôt qu'un
    modèle d'utilisateur personnalisé : Django ne permet pas d'en changer sur
    une base déjà en service."""

    utilisateur = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membre")
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="membres", verbose_name="agence")

    class Meta:
        verbose_name = "membre"

    def __str__(self):
        return f"{self.utilisateur} · {self.organisation}"


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
