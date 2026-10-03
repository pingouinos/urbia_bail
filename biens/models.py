"""Référentiel des bailleurs et des biens (lots) gérés par l'agence.

Les champs reprennent ce que le bail type (décret n° 2015-587) demande de
décrire. La référence ICS du lot sert de clé pour les imports successifs et
pour la future migration du logiciel de gestion.
"""

from django.db import models
from django.urls import reverse
from simple_history.models import HistoricalRecords


class Horodatage(models.Model):
    cree_le = models.DateTimeField("créé le", auto_now_add=True)
    modifie_le = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        abstract = True


class Bailleur(Horodatage):
    class Type(models.TextChoices):
        PERSONNE_PHYSIQUE = "physique", "Personne physique"
        SCI_FAMILIALE = "sci_familiale", "SCI familiale"
        PERSONNE_MORALE = "morale", "Personne morale"

    type = models.CharField(max_length=20, choices=Type.choices, default=Type.PERSONNE_PHYSIQUE)
    nom = models.CharField("nom ou raison sociale", max_length=200)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    ref_ics = models.CharField(
        "référence ICS", max_length=50, unique=True, null=True, blank=True
    )
    adresse = models.CharField(max_length=255, blank=True)
    code_postal = models.CharField("code postal", max_length=10, blank=True)
    ville = models.CharField(max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=30, blank=True)
    siren = models.CharField("SIREN", max_length=14, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["nom", "prenom"]
        verbose_name = "bailleur"

    def __str__(self):
        return f"{self.nom} {self.prenom}".strip()

    def get_absolute_url(self):
        return reverse("biens:bailleur", args=[self.pk])

    @property
    def duree_bail_nu_ans(self):
        """3 ans pour une personne physique ou une SCI familiale, 6 ans sinon."""
        return 6 if self.type == self.Type.PERSONNE_MORALE else 3


class Bien(Horodatage):
    class Usage(models.TextChoices):
        HABITATION = "habitation", "Habitation"
        PROFESSIONNEL = "professionnel", "Local professionnel"
        COMMERCIAL = "commercial", "Local commercial"
        STATIONNEMENT = "stationnement", "Parking ou garage"

    class TypeHabitat(models.TextChoices):
        COLLECTIF = "collectif", "Immeuble collectif"
        INDIVIDUEL = "individuel", "Individuel"

    class Regime(models.TextChoices):
        COPROPRIETE = "copropriete", "Copropriété"
        MONOPROPRIETE = "monopropriete", "Monopropriété"

    class Periode(models.TextChoices):
        AVANT_1949 = "avant_1949", "Avant 1949"
        DE_1949_A_1974 = "1949_1974", "De 1949 à 1974"
        DE_1975_A_1989 = "1975_1989", "De 1975 à 1989"
        DE_1990_A_2005 = "1990_2005", "De 1990 à 2005"
        APRES_2005 = "apres_2005", "Depuis 2005"

    class Mode(models.TextChoices):
        INDIVIDUEL = "individuel", "Individuel"
        COLLECTIF = "collectif", "Collectif"

    class ClasseDPE(models.TextChoices):
        A = "A", "A"
        B = "B", "B"
        C = "C", "C"
        D = "D", "D"
        E = "E", "E"
        F = "F", "F"
        G = "G", "G"
        NON_SOUMIS = "NS", "Non soumis"

    # Identification
    ref_ics = models.CharField(
        "référence ICS du lot", max_length=50, unique=True, null=True, blank=True
    )
    bailleur = models.ForeignKey(Bailleur, on_delete=models.PROTECT, related_name="biens")
    usage = models.CharField(max_length=20, choices=Usage.choices, default=Usage.HABITATION)
    meuble = models.BooleanField("meublé", default=False)
    actif = models.BooleanField(
        default=True, help_text="Décocher pour un bien qui n'est plus géré par l'agence."
    )

    # Localisation
    adresse = models.CharField(max_length=255)
    complement = models.CharField(
        "complément", max_length=255, blank=True, help_text="Bâtiment, escalier, étage, porte."
    )
    code_postal = models.CharField("code postal", max_length=10)
    ville = models.CharField(max_length=100)

    # Description
    type_habitat = models.CharField(
        "type d'habitat", max_length=20, choices=TypeHabitat.choices, blank=True
    )
    regime_juridique = models.CharField(
        "régime juridique de l'immeuble", max_length=20, choices=Regime.choices, blank=True
    )
    periode_construction = models.CharField(
        "période de construction", max_length=20, choices=Periode.choices, blank=True
    )
    surface = models.DecimalField(
        "surface habitable (m²)", max_digits=7, decimal_places=2, null=True, blank=True
    )
    nb_pieces = models.PositiveSmallIntegerField(
        "nombre de pièces principales", null=True, blank=True
    )

    # Équipements
    equipements = models.TextField("équipements du logement", blank=True)
    chauffage = models.CharField(
        "production de chauffage", max_length=20, choices=Mode.choices, blank=True
    )
    eau_chaude = models.CharField(
        "production d'eau chaude", max_length=20, choices=Mode.choices, blank=True
    )
    acces_tic = models.CharField(
        "accès aux technologies de l'information", max_length=255, blank=True,
        help_text="Raccordement fibre, antenne collective…",
    )
    annexes = models.TextField(
        "locaux et équipements privatifs", blank=True, help_text="Cave, parking, jardin…"
    )
    parties_communes = models.TextField("parties et équipements communs", blank=True)

    # Énergie
    classe_dpe = models.CharField("classe DPE", max_length=2, choices=ClasseDPE.choices, blank=True)
    depenses_energie_min = models.PositiveIntegerField(
        "dépenses annuelles d'énergie estimées, minimum (€)", null=True, blank=True
    )
    depenses_energie_max = models.PositiveIntegerField(
        "dépenses annuelles d'énergie estimées, maximum (€)", null=True, blank=True
    )
    date_dpe = models.DateField("date du DPE", null=True, blank=True)

    # Loyer
    zone_tendue = models.BooleanField("zone tendue", default=False)
    loyer_reference_majore = models.DecimalField(
        "loyer de référence majoré (€/m²)", max_digits=7, decimal_places=2, null=True, blank=True,
        help_text="Seulement si la commune applique l'encadrement des loyers.",
    )
    dernier_loyer = models.DecimalField(
        "dernier loyer hors charges (€)", max_digits=9, decimal_places=2, null=True, blank=True
    )
    charges = models.DecimalField(
        "provision sur charges (€)", max_digits=9, decimal_places=2, null=True, blank=True
    )

    observations = models.TextField(blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["ville", "adresse", "complement"]
        verbose_name = "bien"

    def __str__(self):
        morceaux = [self.adresse, self.complement, f"{self.code_postal} {self.ville}"]
        return ", ".join(m for m in morceaux if m)

    def get_absolute_url(self):
        return reverse("biens:bien", args=[self.pk])

    @property
    def est_habitation(self):
        return self.usage == self.Usage.HABITATION

    @property
    def location_interdite(self):
        """Un logement classé G ne peut plus faire l'objet d'un nouveau bail
        depuis le 1er janvier 2025 (critère de décence énergétique)."""
        return self.est_habitation and self.classe_dpe == self.ClasseDPE.G

    def champs_manquants_pour_bail(self):
        """Mentions du bail type d'habitation encore vides sur la fiche."""
        if not self.est_habitation:
            return []
        obligatoires = [
            "type_habitat", "regime_juridique", "periode_construction", "surface",
            "nb_pieces", "chauffage", "eau_chaude", "classe_dpe",
        ]
        return [
            self._meta.get_field(nom).verbose_name
            for nom in obligatoires
            if getattr(self, nom) in (None, "")
        ]

