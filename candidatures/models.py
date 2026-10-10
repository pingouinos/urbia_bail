"""Candidatures à la location d'un logement.

Le dossier complet (pièces justificatives) reste sur DossierFacile : la fiche
ne garde que le lien et ce qu'il faut pour décider puis rédiger le bail. On
n'y enregistre ni coordonnées bancaires, ni situation de famille, ni
nationalité (art. 22-2 de la loi du 6 juillet 1989, décret n° 2015-1437).

Les données des candidats non retenus sont effacées trois mois après la
décision, comme le recommande le référentiel CNIL de la gestion locative
(délibération n° 2021-057, point 8.1).
"""

import datetime as dt
import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from baux.models import Bail
from biens.models import Bailleur, Bien, Horodatage

DUREE_CONSERVATION = dt.timedelta(days=90)
# Durée de validité du lien envoyé au candidat pour compléter ses
# informations.
DUREE_LIEN = dt.timedelta(days=14)


def valider_lien_dossierfacile(valeur):
    hote = urlparse(valeur).hostname or ""
    if not (hote == "dossierfacile.logement.gouv.fr" or hote.endswith(".dossierfacile.logement.gouv.fr")
            or hote == "dossierfacile.fr" or hote.endswith(".dossierfacile.fr")):
        raise ValidationError("Indiquer le lien de partage fourni par DossierFacile.")


class Candidature(Horodatage):
    class Statut(models.TextChoices):
        A_ETUDIER = "a_etudier", "À étudier"
        RETENUE = "retenue", "Retenue"
        NON_RETENUE = "non_retenue", "Non retenue"
        DESISTEMENT = "desistement", "Désistement du candidat"

    class Garantie(models.TextChoices):
        AUCUNE = "aucune", "Aucune"
        PERSONNE = "personne", "Caution personne physique"
        VISALE = "visale", "Visale"
        AUTRE = "autre", "Autre garantie"

    bien = models.ForeignKey(Bien, on_delete=models.PROTECT, related_name="candidatures")
    lien_dossierfacile = models.URLField(
        "lien DossierFacile", blank=True, validators=[valider_lien_dossierfacile],
        help_text="Lien de partage envoyé par le candidat.",
    )
    dossier_verifie = models.BooleanField(
        "dossier consulté sur DossierFacile", default=False,
        help_text="Cocher une fois le dossier ouvert et les pièces vérifiées.",
    )
    garantie = models.CharField(max_length=10, choices=Garantie.choices, default=Garantie.AUCUNE)
    date_entree_souhaitee = models.DateField("entrée souhaitée", null=True, blank=True)
    notes = models.TextField(
        "notes internes", blank=True,
        help_text="Jamais communiquées au candidat. Ne pas y noter de critère interdit.",
    )

    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.A_ETUDIER)
    decide_le = models.DateTimeField("décidé le", null=True, blank=True)
    decide_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="décidé par",
    )
    refus_envoye_le = models.DateTimeField("réponse envoyée le", null=True, blank=True)
    bail = models.OneToOneField(
        Bail, on_delete=models.SET_NULL, null=True, blank=True, related_name="candidature"
    )

    # Lien personnel envoyé au candidat, dès l'étude de sa candidature ou une
    # fois retenu : il y complète son identité, ses coordonnées et le lien de
    # son dossier DossierFacile. Le jeton est effacé dès que le formulaire est
    # envoyé.
    jeton = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False)
    lien_cree_le = models.DateTimeField("lien envoyé le", null=True, blank=True)
    rempli_le = models.DateTimeField("rempli par le locataire le", null=True, blank=True)

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = "candidature"

    def __str__(self):
        return f"{self.noms or 'Candidature'} · {self.bien.adresse}"

    def get_absolute_url(self):
        return reverse("candidatures:candidature", args=[self.pk])

    @property
    def noms(self):
        return ", ".join(str(candidat) for candidat in self.candidats.all())

    @property
    def email(self):
        candidat = self.candidats.exclude(email="").first()
        return candidat.email if candidat else ""

    @property
    def revenus(self):
        return sum((c.revenus_mensuels or 0) for c in self.candidats.all())

    @property
    def taux_effort(self):
        """Part des revenus mensuels nets consacrée au loyer charges comprises,
        en pourcentage, sur la base du dernier loyer du bien."""
        loyer = (self.bien.dernier_loyer or 0) + (self.bien.charges or 0)
        if not loyer or not self.revenus:
            return None
        return round(loyer * 100 / self.revenus)

    @property
    def alertes(self):
        alertes = []
        mandat = self.bien.mandat_en_cours
        if (self.garantie == self.Garantie.PERSONNE and mandat and mandat.assurance_loyers_impayes
                and not self.candidats.filter(
                    contrat__in=[Candidat.Contrat.ETUDIANT, Candidat.Contrat.APPRENTI]).exists()):
            alertes.append(
                "Le mandat prévoit une assurance loyers impayés : le bailleur ne peut pas exiger en plus "
                "une caution, sauf pour un étudiant ou un apprenti (art. 22-1 de la loi du 6 juillet 1989)."
            )
        if self.bien.location_interdite:
            alertes.append("Logement classé G au DPE : il ne peut plus être loué comme résidence principale.")
        return alertes

    @property
    def decidee(self):
        return self.statut != self.Statut.A_ETUDIER

    @property
    def date_effacement(self):
        """Date à partir de laquelle la purge efface la candidature (voir
        a_effacer), ou None si elle est devenue un bail."""
        if self.bail_id:
            return None
        depart = self.modifie_le if self.statut == self.Statut.A_ETUDIER else self.decide_le
        return timezone.localdate(depart + DUREE_CONSERVATION) if depart else None

    def decider(self, statut, utilisateur):
        self.statut = statut
        self.decide_le = None if statut == self.Statut.A_ETUDIER else timezone.now()
        self.decide_par = None if statut == self.Statut.A_ETUDIER else utilisateur
        self.save(update_fields=["statut", "decide_le", "decide_par", "modifie_le"])

    @property
    def lien_expire_le(self):
        return self.lien_cree_le + DUREE_LIEN if self.lien_cree_le else None

    @property
    def lien_possible(self):
        """Le candidat peut recevoir un lien tant que sa candidature est à
        l'étude ou retenue, et que le bail n'est pas rédigé."""
        return self.statut in (self.Statut.A_ETUDIER, self.Statut.RETENUE) and not self.bail_id

    @property
    def lien_valide(self):
        return bool(self.jeton and self.lien_possible and timezone.now() < self.lien_expire_le)

    def creer_lien(self):
        """Nouveau lien pour le candidat ; l'ancien cesse de fonctionner."""
        self.jeton = secrets.token_urlsafe(32)
        self.lien_cree_le = timezone.now()
        self.save(update_fields=["jeton", "lien_cree_le", "modifie_le"])

    @classmethod
    def par_jeton(cls, jeton):
        """Candidature dont le lien est encore valable, ou None."""
        candidature = cls.objects.select_related("bien").filter(jeton=jeton).first() if jeton else None
        return candidature if candidature and candidature.lien_valide else None

    def texte_lien(self, lien, signataire=""):
        bien = self.bien
        logement = f"le logement situé {bien.adresse}, {bien.code_postal} {bien.ville}"
        if self.statut == self.Statut.RETENUE:
            debut = f"Votre candidature pour {logement} a été retenue. Pour préparer votre bail"
        else:
            debut = f"Vous êtes candidat à la location pour {logement}. Pour que nous étudiions votre dossier"
        return (
            "Bonjour,\n\n"
            f"{debut}, merci de compléter vos informations (identité, "
            "coordonnées) et d'indiquer le lien de partage de votre dossier DossierFacile, à l'adresse "
            "suivante :\n\n"
            f"{lien}\n\n"
            f"Ce lien vous est personnel et reste valable jusqu'au "
            f"{timezone.localtime(self.lien_expire_le):%d/%m/%Y}.\n\n"
            "Cordialement,\n"
            + (f"{signataire}\n" if signataire else "")
            + "URBIA Immobilier"
        )

    @classmethod
    def a_effacer(cls, maintenant=None):
        """Candidatures non retenues (ou abandonnées) depuis plus de trois
        mois, candidatures retenues sans bail trois mois après la décision, et
        candidatures restées sans décision ni modification depuis trois mois.
        Une candidature devenue bail est gardée : le locataire est au dossier."""
        limite = (maintenant or timezone.now()) - DUREE_CONSERVATION
        return cls.objects.filter(
            models.Q(statut__in=[cls.Statut.NON_RETENUE, cls.Statut.DESISTEMENT], decide_le__lt=limite)
            | models.Q(statut=cls.Statut.RETENUE, bail__isnull=True, decide_le__lt=limite)
            | models.Q(statut=cls.Statut.A_ETUDIER, modifie_le__lt=limite)
        )

    @classmethod
    def purger(cls, maintenant=None):
        _, detail = cls.a_effacer(maintenant).delete()
        return detail.get(cls._meta.label, 0)

    def texte_refus(self, signataire=""):
        bien = self.bien
        return (
            "Madame, Monsieur,\n\n"
            f"Nous vous remercions de l'intérêt que vous avez porté au logement situé {bien.adresse}, "
            f"{bien.code_postal} {bien.ville}. Après étude des dossiers reçus, nous sommes au regret de "
            "vous informer que votre candidature n'a pas été retenue.\n\n"
            "Les informations vous concernant seront supprimées de nos fichiers dans un délai de trois "
            "mois. Vous pouvez en demander l'effacement immédiat en répondant à ce message.\n\n"
            "Nous vous souhaitons pleine réussite dans votre recherche.\n\n"
            "Cordialement,\n"
            + (f"{signataire}\n" if signataire else "")
            + "URBIA Immobilier"
        )


class Candidat(models.Model):
    class Contrat(models.TextChoices):
        CDI = "cdi", "CDI"
        CDD = "cdd", "CDD"
        INTERIM = "interim", "Intérim"
        INDEPENDANT = "independant", "Indépendant"
        FONCTIONNAIRE = "fonctionnaire", "Fonctionnaire"
        ETUDIANT = "etudiant", "Étudiant"
        APPRENTI = "apprenti", "Apprenti"
        RETRAITE = "retraite", "Retraité"
        AUTRE = "autre", "Autre"

    candidature = models.ForeignKey(Candidature, on_delete=models.CASCADE, related_name="candidats")
    civilite = models.CharField("civilité", max_length=5, choices=Bailleur.Civilite.choices, blank=True)
    nom = models.CharField(max_length=100)
    prenom = models.CharField("prénom", max_length=100)
    date_naissance = models.DateField("date de naissance", null=True, blank=True)
    lieu_naissance = models.CharField("lieu de naissance", max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=30, blank=True)
    profession = models.CharField(max_length=100, blank=True)
    employeur = models.CharField(max_length=150, blank=True)
    contrat = models.CharField("type de contrat", max_length=15, choices=Contrat.choices, blank=True)
    date_embauche = models.DateField("date d'embauche", null=True, blank=True)
    revenus_mensuels = models.DecimalField(
        "revenus mensuels nets (€)", max_digits=9, decimal_places=2, null=True, blank=True
    )

    class Meta:
        ordering = ["pk"]
        verbose_name = "candidat"

    def __str__(self):
        return f"{self.prenom} {self.nom.upper()}"


class Garant(models.Model):
    """Caution personne physique d'un candidat, déclarée par lui dans le
    formulaire reçu par lien. Ses pièces sont dans le dossier DossierFacile."""

    candidat = models.ForeignKey(Candidat, on_delete=models.CASCADE, related_name="garants")
    civilite = models.CharField("civilité", max_length=5, choices=Bailleur.Civilite.choices, blank=True)
    nom = models.CharField(max_length=100)
    prenom = models.CharField("prénom", max_length=100)
    adresse = models.CharField("adresse complète", max_length=255, help_text="Rue, code postal et ville.")
    email = models.EmailField("e-mail", blank=True)
    telephone = models.CharField("téléphone", max_length=30, blank=True)

    class Meta:
        ordering = ["pk"]
        verbose_name = "garant"

    def __str__(self):
        return f"{self.prenom} {self.nom.upper()}"

    @property
    def ligne_bail(self):
        """Désignation reprise dans le bail : « Madame BERNARD Claire, 3 rue
        d'Alsace, 31000 Toulouse »."""
        identite = " ".join(m for m in (self.get_civilite_display(), self.nom.upper(), self.prenom) if m)
        return f"{identite}, {self.adresse}"
