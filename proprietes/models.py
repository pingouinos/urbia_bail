"""Référentiel des bailleurs et des biens (lots) gérés par l'agence.

Les champs reprennent ce que le bail type (décret n° 2015-587) demande de
décrire. La référence ICS du lot sert de clé pour les imports successifs et
pour la future migration du logiciel de gestion.
"""

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from types import SimpleNamespace
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from core.champs import TelephoneField, code_postal_francais, siren_valide
from core.fichiers import nom_aleatoire


@dataclass(frozen=True)
class Situation:
    """Situation locative d'un bien, d'après les baux enregistrés."""

    code: str  # loue, preavis, libre ou sans_bail
    libelle: str
    bail: object = None  # bail du locataire en place
    a_venir: object = None  # bail rédigé dont la prise d'effet est à venir



def baux_en_place(jour=None):
    """Baux dont le locataire occupe le bien ce jour-là, à rapprocher de
    chaque bien par ``Exists`` : un bien sans bail en place est libre."""
    from contrats.models import Bail

    jour = jour or timezone.localdate()
    return Bail.objects.filter(propriete=models.OuterRef("pk"), date_effet__lte=jour).filter(
        models.Q(date_fin_effective__isnull=True) | models.Q(date_fin_effective__gt=jour)
    )

# Abréviations courantes des voies, pour reconnaître une même adresse.
VOIES = {
    "av": "avenue", "ave": "avenue", "bd": "boulevard", "bld": "boulevard", "boul": "boulevard", "pl": "place",
    "all": "allee", "ch": "chemin", "che": "chemin", "rte": "route", "imp": "impasse", "sq": "square",
    "fg": "faubourg", "st": "saint", "ste": "sainte",
}


def cle_adresse(texte):
    """Adresse ramenée à l'essentiel pour la comparer : sans accents,
    majuscules ni ponctuation, abréviations de voies développées.
    « 302, Av. de Muret » et « 302 avenue de muret » donnent la même clé."""
    sans_accents = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()
    mots = re.sub(r"[^a-z0-9]+", " ", sans_accents).split()
    return " ".join(VOIES.get(mot, mot) for mot in mots)


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

    class Civilite(models.TextChoices):
        MONSIEUR = "M.", "Monsieur"
        MADAME = "Mme", "Madame"

    type = models.CharField(max_length=20, choices=Type.choices, default=Type.PERSONNE_PHYSIQUE)
    civilite = models.CharField("civilité", max_length=5, choices=Civilite.choices, blank=True)
    nom = models.CharField("nom ou raison sociale", max_length=200)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    # Second propriétaire (couple, indivision) mentionné comme co-mandant.
    conjoint_civilite = models.CharField(
        "civilité du conjoint", max_length=5, choices=Civilite.choices, blank=True
    )
    conjoint_nom = models.CharField("nom du conjoint ou co-propriétaire", max_length=200, blank=True)
    conjoint_prenom = models.CharField("prénom du conjoint", max_length=100, blank=True)
    ref_ics = models.CharField(
        "référence ICS", max_length=50, unique=True, null=True, blank=True
    )
    adresse = models.CharField(max_length=255, blank=True)
    code_postal = models.CharField("code postal", max_length=10, blank=True)
    ville = models.CharField(max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = TelephoneField("téléphone", blank=True)
    siren = models.CharField("SIREN", max_length=20, blank=True, validators=[siren_valide])
    # Personne qui signe pour une société (SCI, personne morale).
    representant_civilite = models.CharField(
        "civilité du représentant", max_length=5, choices=Civilite.choices, blank=True
    )
    representant_nom = models.CharField("nom du représentant", max_length=200, blank=True)
    representant_prenom = models.CharField("prénom du représentant", max_length=100, blank=True)
    representant_qualite = models.CharField(
        "qualité du représentant", max_length=60, blank=True, help_text="Gérant, gérante, président…",
    )
    # La personne, quelle que soit la ligne par laquelle elle apparaît.
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name="bailleurs",
    )
    representant = models.ForeignKey(
        "contacts.Contact", on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name="societes",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["nom", "prenom"]
        verbose_name = "bailleur"

    @property
    def societe(self):
        return self.type != self.Type.PERSONNE_PHYSIQUE

    @property
    def lien_annuaire(self):
        """Fiche de la société sur l'Annuaire des Entreprises de l'État, où
        son extrait d'immatriculation au RNE se télécharge gratuitement."""
        siren = re.sub(r"\D", "", self.siren or "")[:9]
        return f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}" if len(siren) == 9 else ""

    @classmethod
    def deja_enregistre(cls, type_, nom, prenom="", siren="", code_postal="", sauf=None):
        """Bailleur qui est manifestement le même, ou None. Une société se
        reconnaît à sa raison sociale, sauf SIREN différents ; une personne à
        son nom et son prénom, sauf codes postaux différents (deux homonymes)."""
        societe = type_ != cls.Type.PERSONNE_PHYSIQUE
        candidats = cls.objects.exclude(pk=sauf)
        candidats = candidats.exclude(type=cls.Type.PERSONNE_PHYSIQUE) if societe else candidats.filter(type=cls.Type.PERSONNE_PHYSIQUE)
        cle = cle_adresse(nom), "" if societe else cle_adresse(prenom)

        def distincts(a, b):
            a, b = re.sub(r"\s", "", a or ""), re.sub(r"\s", "", b or "")
            return bool(a and b and a != b)

        for autre in candidats.order_by("pk"):
            if (cle_adresse(autre.nom), "" if societe else cle_adresse(autre.prenom)) != cle:
                continue
            if distincts(siren, autre.siren) if societe else distincts(code_postal, autre.code_postal):
                continue
            return autre
        return None

    def save(self, *args, **kwargs):
        from contacts.models import Contact, cle_identite

        if self.societe:
            # Le nom est celui de la société ; la personne saisie avec elle est son représentant.
            if self.prenom and not self.representant_prenom:
                self.representant_civilite = self.representant_civilite or self.civilite
                self.representant_prenom = self.prenom
            self.civilite = self.prenom = ""
        # La fiche du bailleur fait foi pour l'identité et les coordonnées de son contact.
        self.contact = Contact.rattacher(self, automatique=False, synchroniser=True)
        if self.societe and self.representant_nom:
            cle = cle_identite(self.representant_nom, self.representant_prenom)
            deja = self.representant if self.representant_id and self.representant.cle == cle else None
            self.representant = Contact.rattacher(SimpleNamespace(
                nom=self.representant_nom, prenom=self.representant_prenom, civilite=self.representant_civilite,
                contact=deja, contact_id=deja and deja.pk,
            ), automatique=False)
        else:
            self.representant = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nom if self.societe else f"{self.nom} {self.prenom}".strip()

    def get_absolute_url(self):
        return reverse("proprietes:bailleur", args=[self.pk])

    @property
    def designation(self):
        """Désignation complète, telle qu'elle figure en tête d'un mandat ou
        d'un bail : « Monsieur DURAND Paul et Madame MARTIN Anne », ou « SCI
        DU CANAL, représentée par Madame DURAND Anne, gérante »."""

        def personne(civilite, nom, prenom):
            libelle = self.Civilite(civilite).label if civilite else ""
            return " ".join(m for m in (libelle, nom.upper(), prenom) if m)

        if self.societe:
            if not self.representant_nom:
                return self.nom
            representant = personne(self.representant_civilite, self.representant_nom, self.representant_prenom)
            accord = "représentée" if self.type == self.Type.SCI_FAMILIALE else "représenté(e)"
            qualite = f", {self.representant_qualite}" if self.representant_qualite else ""
            return f"{self.nom}, {accord} par {representant}{qualite}"

        titulaire = personne(self.civilite, self.nom, self.prenom)
        if not self.conjoint_nom:
            return titulaire
        return f"{titulaire} et {personne(self.conjoint_civilite, self.conjoint_nom, self.conjoint_prenom)}"

    @property
    def duree_bail_nu_ans(self):
        """3 ans pour une personne physique ou une SCI familiale, 6 ans sinon."""
        return 6 if self.type == self.Type.PERSONNE_MORALE else 3


class Associe(models.Model):
    """Associé d'une société bailleresse (SCI, personne morale)."""

    bailleur = models.ForeignKey(Bailleur, on_delete=models.CASCADE, related_name="associes")
    civilite = models.CharField("civilité", max_length=5, choices=Bailleur.Civilite.choices, blank=True)
    nom = models.CharField(max_length=200)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    parts = models.DecimalField(
        "parts (%)", max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name="associes",
    )

    class Meta:
        ordering = ["-parts", "nom", "prenom"]
        verbose_name = "associé"

    def __str__(self):
        return f"{self.nom.upper()} {self.prenom}".strip()

    def save(self, *args, **kwargs):
        from contacts.models import Contact, cle_identite

        # L'associé garde son contact tant que son nom ne change pas.
        if self.contact_id and self.contact.cle != cle_identite(self.nom, self.prenom):
            self.contact = None
        self.contact = Contact.rattacher(self, automatique=False)
        super().save(*args, **kwargs)


class Immeuble(Horodatage):
    """Bâtiment dont l'agence gère plusieurs lots. Il regroupe des propriétés
    à la même adresse ; chaque lot garde son bailleur, car une copropriété
    réunit plusieurs propriétaires."""

    nom = models.CharField(
        "nom de l'immeuble", max_length=120, blank=True, help_text="Résidence, bâtiment… Facultatif.",
    )
    adresse = models.CharField(max_length=255)
    code_postal = models.CharField("code postal", max_length=10, validators=[code_postal_francais])
    ville = models.CharField(max_length=100)

    # Ce qui fait qu'un immeuble est le même : adresse, code postal, ville et
    # nom, sans tenir compte de l'écriture. Unique, pour qu'un immeuble ne
    # soit pas enregistré deux fois.
    cle = models.CharField(max_length=600, editable=False, default="")

    history = HistoricalRecords()

    class Meta:
        ordering = ["ville", "adresse", "nom"]
        verbose_name = "immeuble"
        constraints = [models.UniqueConstraint(fields=["cle"], name="immeuble_unique")]

    def __str__(self):
        return self.nom or f"{self.adresse}, {self.code_postal} {self.ville}"

    def save(self, *args, **kwargs):
        self.cle = self.identite(self.nom, self.adresse, self.code_postal, self.ville)
        super().save(*args, **kwargs)

    @staticmethod
    def identite(nom, adresse, code_postal, ville):
        """« 2 Rue Jean Mermoz » et « 2 rue jean mermoz, » à Portet-sur-Garonne
        désignent le même immeuble ; deux bâtiments d'une même adresse se
        distinguent par leur nom."""
        return "|".join([cle_adresse(adresse), (code_postal or "").strip(), cle_adresse(ville), cle_adresse(nom)])[:600]

    @property
    def designation(self):
        """Libellé sans ambiguïté pour une liste de choix : « MERMOZ — 2 Rue
        Jean Mermoz, 31120 Portet-sur-Garonne »."""
        adresse = f"{self.adresse}, {self.code_postal} {self.ville}"
        return f"{self.nom} — {adresse}" if self.nom else adresse

    def get_absolute_url(self):
        return reverse("proprietes:immeuble", args=[self.pk])

    @classmethod
    def a_l_adresse(cls, adresse, code_postal):
        """Immeubles déjà enregistrés à cette adresse."""
        cle = cle_adresse(adresse)
        return [i for i in cls.objects.filter(code_postal=(code_postal or "").strip()) if cle_adresse(i.adresse) == cle]

    def aligner_les_lots(self):
        """Reporte l'adresse de l'immeuble sur ses lots, après une correction."""
        for lot in self.lots.all():
            if (lot.adresse, lot.code_postal, lot.ville) != (self.adresse, self.code_postal, self.ville):
                lot.adresse, lot.code_postal, lot.ville = self.adresse, self.code_postal, self.ville
                lot.save()


class Propriete(Horodatage):
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
    bailleur = models.ForeignKey(Bailleur, on_delete=models.PROTECT, related_name="proprietes")
    immeuble = models.ForeignKey(
        Immeuble, on_delete=models.SET_NULL, null=True, blank=True, related_name="lots",
        help_text="À choisir si ce lot fait partie d'un immeuble dont l'agence gère plusieurs lots.",
    )
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
    code_postal = models.CharField("code postal", max_length=10, validators=[code_postal_francais])
    ville = models.CharField(max_length=100)

    etage = models.CharField("étage", max_length=20, blank=True)
    numero_appartement = models.CharField("n° d'appartement", max_length=20, blank=True)
    numero_parking = models.CharField("n° de parking", max_length=20, blank=True)
    numero_cellier = models.CharField("n° de cave ou cellier", max_length=20, blank=True)
    identifiant_fiscal = models.CharField(
        "identifiant fiscal du logement", max_length=20, blank=True,
        help_text="Visible dans l'espace « Gérer mes biens immobiliers » du propriétaire sur impots.gouv.fr.",
    )
    syndic = models.CharField(
        max_length=200, blank=True, help_text="Syndic de l'immeuble, si ce n'est pas l'agence."
    )

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
    detecteur_fumee = models.BooleanField("détecteur de fumée installé", default=False)

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
    zone_tendue = models.BooleanField(
        "zone tendue", default=False,
        help_text="Déduite de la commune. À cocher seulement si la commune est introuvable dans la liste officielle.",
    )
    # Vrai quand la zone tendue vient de la liste officielle des communes
    # (proprietes.zones_tendues) et non d'une saisie.
    commune_reconnue = models.BooleanField(default=False, editable=False)
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
    dispositif_fiscal = models.CharField(
        "dispositif fiscal et n° de convention", max_length=200, blank=True,
        help_text="Pinel, Denormandie, Loc'Avantages… Le loyer peut alors être plafonné.",
    )

    observations = models.TextField(blank=True)

    # Mise en location : ouverte quand le logement se libère, refermée à la
    # rédaction du bail suivant.
    mise_en_location_le = models.DateField("mis en location le", null=True, blank=True, editable=False)
    disponible_le = models.DateField(
        "disponible le", null=True, blank=True, help_text="Date à laquelle le nouveau locataire peut entrer.",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["ville", "adresse", "complement"]
        verbose_name = "propriété"

    def save(self, *args, **kwargs):
        self.appliquer_zone_tendue()
        super().save(*args, **kwargs)

    def appliquer_zone_tendue(self):
        """La zone tendue suit la commune quand la liste officielle la
        connaît ; sinon la saisie est gardée."""
        from .zones_tendues import statut

        tendue, _ = statut(self.code_postal, self.ville)
        self.commune_reconnue = tendue is not None
        if tendue is not None:
            self.zone_tendue = tendue

    @property
    def commune_officielle(self):
        """La commune de la liste officielle, ou None si elle est introuvable."""
        from .zones_tendues import statut

        return statut(self.code_postal, self.ville)[1]

    def __str__(self):
        morceaux = [self.adresse, self.complement, f"{self.code_postal} {self.ville}"]
        return ", ".join(m for m in morceaux if m)

    def get_absolute_url(self):
        return reverse("proprietes:propriete", args=[self.pk])

    @classmethod
    def choix_pour(cls, contrat):
        """Les biens proposés dans un mandat ou un bail : ceux que l'agence
        gère, plus celui du contrat lui-même, même décoché « Actif » depuis,
        pour qu'il s'affiche toujours et que le contrat s'enregistre."""
        filtre = models.Q(actif=True)
        if contrat.propriete_id:
            filtre |= models.Q(pk=contrat.propriete_id)
        return cls.objects.filter(filtre).select_related("bailleur")

    @classmethod
    def a_l_adresse(cls, adresse, code_postal, sauf=None):
        """Propriétés déjà enregistrées à cette adresse, quel que soit le lot."""
        cle = cle_adresse(adresse)
        memes = cls.objects.filter(code_postal=(code_postal or "").strip()).select_related("bailleur", "immeuble")
        return [p for p in memes.exclude(pk=sauf) if cle_adresse(p.adresse) == cle]

    @property
    def cle_lot(self):
        """Ce qui distingue un lot d'un autre à la même adresse."""
        return cle_adresse(self.complement), cle_adresse(self.numero_appartement)

    @property
    def mandat_en_cours(self):
        return self.mandats.filter(date_fin__isnull=True).order_by("-numero").first()

    @property
    def a_louer(self):
        return self.mise_en_location_le is not None

    @property
    def situation(self):
        """Loué, en préavis ou libre. Un bien dont aucun bail n'est dans
        l'application est « sans bail enregistré » : il peut être loué par un
        bail signé avant sa mise en service."""
        aujourd_hui = timezone.localdate()
        baux = sorted(self.baux.all(), key=lambda bail: bail.date_effet)
        actifs = [b for b in baux if b.date_fin_effective is None or b.date_fin_effective > aujourd_hui]
        en_place = [b for b in actifs if b.date_effet <= aujourd_hui]
        a_venir = next((b for b in actifs if b.date_effet > aujourd_hui), None)
        if not en_place:
            if baux:
                return Situation("libre", "Libre", a_venir=a_venir)
            return Situation("sans_bail", "Aucun bail enregistré")
        bail = en_place[-1]
        if bail.conge_recu_le:
            return Situation("preavis", f"Préavis, libre le {bail.date_depart_prevue:%d/%m/%Y}", bail, a_venir)
        return Situation("loue", "Loué", bail, a_venir)

    @property
    def designation(self):
        """Description courte pour un mandat : « Appartement T2 meublé de 39 m² »."""
        if self.usage == self.Usage.STATIONNEMENT:
            return "Un emplacement de stationnement"
        if not self.est_habitation:
            return f"Un {self.get_usage_display().lower()}"
        nature = "Maison" if self.type_habitat == self.TypeHabitat.INDIVIDUEL else "Appartement"
        morceaux = [nature]
        if self.nb_pieces:
            morceaux.append(f"T{self.nb_pieces}")
        if self.meuble:
            morceaux.append("meublé")
        if self.surface:
            morceaux.append(f"de {Decimal(str(self.surface)).normalize():f} m²".replace(".", ","))
        return " ".join(morceaux)

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


def dossier_piece(piece, nom):
    """Les pièces d'un bailleur (identité, RIB) sont rangées à part de celles des propriétés."""
    return f"{'bailleurs' if piece.bailleur_id else 'proprietes'}/{nom_aleatoire(nom)}"


class Piece(models.Model):
    """Document attaché à une propriété (diagnostics, titre, assurance…) ou
    à un bailleur (pièce d'identité, RIB). Ce sont les pièces que le mandant
    fournit à la signature du mandat, et celles que l'on annexe au bail."""

    class Type(models.TextChoices):
        IDENTITE = "identite", "Pièce d'identité"
        RIB = "rib", "Relevé d'identité bancaire"
        # Extrait d'immatriculation au RNE (gratuit) ou extrait Kbis. Le code est gardé pour les pièces déjà déposées.
        KBIS = "kbis", "Justificatif d'immatriculation de la société"
        STATUTS = "statuts", "Statuts de la société"
        TAXE_FONCIERE = "taxe_fonciere", "Dernière taxe foncière"
        TITRE = "titre", "Attestation de propriété"
        CHARGES = "charges", "Relevé annuel des charges de copropriété"
        ASSURANCE = "assurance", "Attestation d'assurance propriétaire non occupant"
        BAIL = "bail", "Bail en cours"
        ETAT_DES_LIEUX = "etat_des_lieux", "État des lieux"
        DPE = "dpe", "Diagnostic de performance énergétique (DPE)"
        ELECTRICITE = "electricite", "État de l'installation d'électricité"
        SURFACE = "surface", "Attestation de surface (Carrez ou habitable)"
        GAZ = "gaz", "État de l'installation de gaz"
        ERP = "erp", "État des risques (ERP)"
        PLOMB = "plomb", "Constat de risque d'exposition au plomb"
        AMIANTE = "amiante", "Diagnostic amiante"
        REGLEMENT = "reglement", "Règlement de copropriété"
        AUTRE = "autre", "Autre document"

    # Pièces qui concernent la personne du bailleur, et non une propriété.
    DU_BAILLEUR = (Type.IDENTITE, Type.RIB, Type.KBIS, Type.STATUTS)
    # Durée de validité quand elle ne dépend que de la date.
    VALIDITE = {
        Type.DPE: dt.timedelta(days=3653),
        Type.ELECTRICITE: dt.timedelta(days=2192),
        Type.GAZ: dt.timedelta(days=2192),
        Type.ERP: dt.timedelta(days=183),
        Type.ASSURANCE: dt.timedelta(days=366),
    }
    # Les DPE établis avant cette date ne sont plus valables, quel que soit leur âge.
    DPE_ANCIENNE_METHODE = dt.date(2021, 7, 1)

    propriete = models.ForeignKey(Propriete, on_delete=models.CASCADE, null=True, blank=True, related_name="pieces")
    bailleur = models.ForeignKey(Bailleur, on_delete=models.CASCADE, null=True, blank=True, related_name="pieces")
    type = models.CharField(max_length=20, choices=Type.choices)
    libelle = models.CharField("précision", max_length=120, blank=True)
    fichier = models.FileField(upload_to=dossier_piece)
    date_document = models.DateField(
        "établi le", null=True, blank=True, help_text="Date du document ; elle sert à calculer sa fin de validité.",
    )
    cree_le = models.DateTimeField("déposé le", auto_now_add=True)
    depose_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    class Meta:
        ordering = ["type", "-date_document", "-cree_le"]
        verbose_name = "document"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(propriete__isnull=False, bailleur__isnull=True)
                | models.Q(propriete__isnull=True, bailleur__isnull=False),
                name="piece_propriete_ou_bailleur",
            ),
        ]

    def __str__(self):
        return self.libelle or self.get_type_display()

    @property
    def expire_le(self):
        """Fin de validité pour une location, ou None si elle n'est pas
        connue ou si le document n'en a pas."""
        duree = self.VALIDITE.get(self.type)
        if not duree or not self.date_document:
            return None
        if self.type == self.Type.DPE and self.date_document < self.DPE_ANCIENNE_METHODE:
            return dt.date(2024, 12, 31)
        return self.date_document + duree

    @property
    def perime(self):
        return self.expire_le is not None and self.expire_le < timezone.localdate()

    @property
    def cible(self):
        """La propriété ou le bailleur auquel la pièce est attachée."""
        return self.propriete or self.bailleur


class Commune(models.Model):
    """Commune de la liste officielle des zones tendues, téléchargée depuis
    data.gouv.fr (voir proprietes.zones_tendues). Commune à toutes les
    agences."""

    code_insee = models.CharField("code INSEE", max_length=5, primary_key=True)
    nom = models.CharField(max_length=100)
    departement = models.CharField("département", max_length=3)
    cle = models.CharField(max_length=100, editable=False)
    zone_tendue = models.BooleanField("zone tendue", default=False)

    class Meta:
        ordering = ["nom"]
        indexes = [models.Index(fields=["departement", "cle"])]

    def __str__(self):
        return f"{self.nom} ({self.departement})"


class ListeCommunes(models.Model):
    """Chaque téléchargement de la liste des communes, réussi ou non."""

    le = models.DateTimeField("téléchargée le", auto_now_add=True)
    publiee_le = models.DateTimeField("publiée le", null=True, blank=True)
    communes = models.PositiveIntegerField(default=0)
    en_zone_tendue = models.PositiveIntegerField(default=0)
    erreur = models.TextField(blank=True)
    # Biens dont la zone tendue a changé : [{"id", "bien", "zone_tendue"}].
    changements = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-le"]
        verbose_name = "téléchargement de la liste des communes"
