"""Baux d'habitation (résidence principale), rédigés selon les contrats types
du décret n° 2015-587 du 29 mai 2015 : annexe 1 pour le logement nu, annexe
2 pour le logement meublé."""

import calendar
import datetime as dt
from decimal import Decimal

from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from core.champs import TelephoneField
from core.fichiers import nom_aleatoire

from proprietes.models import Propriete
from .base import Contrat
from mandats.models import PLAFOND_BAIL_HORS_ZONE_TENDUE, PLAFOND_BAIL_ZONE_TENDUE, Mandat, controler_honoraires


def ajouter_mois(date, mois):
    annee, mois_ = divmod(date.month - 1 + mois, 12)
    annee += date.year
    jour = min(date.day, calendar.monthrange(annee, mois_ + 1)[1])
    return dt.date(annee, mois_ + 1, jour)


def ranger_bail_signe(bail, nom):
    return f"baux/{nom_aleatoire(nom)}"


def _montant(**options):
    return models.DecimalField(max_digits=9, decimal_places=2, **options)


def lignes(texte):
    """Lignes non vides d'un champ saisi une valeur par ligne."""
    return [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]


class Bail(Contrat):
    TYPE = "Bail"

    class Type(models.TextChoices):
        NU = "nu", "Logement nu"
        MEUBLE = "meuble", "Logement meublé"
        ETUDIANT = "etudiant", "Logement meublé loué à un étudiant (9 mois)"

    class ModeCharges(models.TextChoices):
        PROVISION = "provision", "Provisions sur charges avec régularisation annuelle"
        FORFAIT = "forfait", "Forfait de charges"

    propriete = models.ForeignKey(
        Propriete, on_delete=models.PROTECT, related_name="baux", verbose_name="propriété",
    )
    mandat = models.ForeignKey(
        Mandat, on_delete=models.PROTECT, related_name="baux", null=True, blank=True,
        help_text="Mandat de gestion dont proviennent les honoraires.",
    )
    type = models.CharField("type de bail", max_length=10, choices=Type.choices, default=Type.NU)
    usage_mixte = models.BooleanField(
        "usage mixte professionnel et d'habitation", default=False,
    )
    garants = models.TextField(
        "garants communs", blank=True,
        help_text="Nom et adresse de chaque caution qui garantit tous les locataires, une par ligne.",
    )
    # Colocation au sens de l'article 8-1 de la loi du 6 juillet 1989 : plusieurs
    # locataires qui ne sont pas un couple marié ou pacsé. La solidarité d'un
    # colocataire qui part cesse alors au plus tard six mois après son congé.
    colocation = models.BooleanField(
        "colocation", default=False,
        help_text="Plusieurs locataires qui ne sont ni mariés ni pacsés ensemble.",
    )

    # Durée
    date_effet = models.DateField("date de prise d'effet")

    # Loyer et charges
    loyer = _montant(verbose_name="loyer mensuel hors charges (€)")
    mode_charges = models.CharField(
        "modalité des charges", max_length=10, choices=ModeCharges.choices, default=ModeCharges.PROVISION
    )
    charges = _montant(verbose_name="provision ou forfait de charges mensuel (€)", default=0)
    jour_paiement = models.PositiveSmallIntegerField("jour de paiement", default=1)
    a_echoir = models.BooleanField("payable d'avance (à échoir)", default=True)
    depot_garantie = _montant(verbose_name="dépôt de garantie (€)", default=0)
    irl_trimestre = models.CharField(
        "trimestre de référence de l'IRL", max_length=30, help_text="Par exemple « 2e trimestre 2026 »."
    )
    irl_valeur = models.DecimalField("valeur de l'IRL", max_digits=7, decimal_places=2)

    # Zone tendue : loyer du précédent locataire
    precedent_loyer = _montant(
        verbose_name="dernier loyer du précédent locataire (€)", null=True, blank=True,
        help_text="À renseigner si le précédent locataire est parti depuis moins de 18 mois.",
    )
    precedent_date_versement = models.DateField("date de son dernier versement", null=True, blank=True)
    precedent_date_revision = models.DateField("date de sa dernière révision", null=True, blank=True)
    travaux = models.TextField(
        "travaux depuis le dernier bail", blank=True,
        help_text="Montant et nature des travaux d'amélioration ou de mise en conformité.",
    )

    # Honoraires, repris du mandat
    honoraires_bail_bailleur = _montant(verbose_name="visite, dossier et bail : part bailleur (€ TTC)", default=0)
    honoraires_bail_locataire = _montant(verbose_name="visite, dossier et bail : part locataire (€ TTC)", default=0)
    honoraires_edl_bailleur = _montant(verbose_name="état des lieux : part bailleur (€ TTC)", default=0)
    honoraires_edl_locataire = _montant(verbose_name="état des lieux : part locataire (€ TTC)", default=0)

    conditions_particulieres = models.TextField("autres conditions particulières", blank=True)
    lieu_signature = models.CharField("lieu de signature", max_length=100, default="Toulouse")
    # Fin du bail : le congé reçu ouvre le préavis, au terme duquel le
    # locataire part. Tant qu'aucun congé n'est enregistré, le logement ne
    # peut pas faire l'objet d'un nouveau bail.
    class Origine(models.TextChoices):
        LOCATAIRE = "locataire", "Le locataire (préavis)"
        BAILLEUR = "bailleur", "Le bailleur (vente, reprise, motif légitime)"

    conge_origine = models.CharField(
        "congé donné par", max_length=10, choices=Origine.choices, blank=True,
    )
    conge_recu_le = models.DateField(
        "congé reçu le", null=True, blank=True,
        help_text="Préavis donné par le locataire, ou congé donné par le bailleur.",
    )
    date_depart_prevue = models.DateField(
        "départ prévu le", null=True, blank=True, help_text="Fin du préavis.",
    )
    date_fin_effective = models.DateField(
        "fin effective", null=True, blank=True, help_text="Date de départ du locataire."
    )
    fichier_signe = models.FileField("bail signé (scan)", upload_to=ranger_bail_signe, blank=True)
    # Suivi des loyers : l'échéancier court à partir de ce mois. Sans date, le
    # bail n'est pas suivi (bail ancien dont les loyers sont tenus ailleurs).
    suivi_loyers_depuis = models.DateField("suivi des loyers depuis", null=True, blank=True, editable=False)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-date_effet"]
        verbose_name = "bail"
        verbose_name_plural = "baux"

    def __str__(self):
        noms = ", ".join(str(locataire) for locataire in self.locataires.all())
        return f"{self.propriete} · {noms or 'locataire à renseigner'}"

    def get_absolute_url(self):
        return reverse("contrats:bail", args=[self.pk])

    @classmethod
    def depuis_propriete(cls, propriete):
        """Bail prérempli à partir du bien et de son mandat en cours."""
        mandat = propriete.mandat_en_cours
        bail = cls(
            propriete=propriete, mandat=mandat,
            type=cls.Type.MEUBLE if propriete.meuble else cls.Type.NU,
            loyer=propriete.dernier_loyer, charges=propriete.charges or 0,
        )
        if propriete.dernier_loyer:
            bail.depot_garantie = propriete.dernier_loyer * bail.mois_depot_max
        if mandat:
            for champ in ("honoraires_bail_bailleur", "honoraires_bail_locataire",
                          "honoraires_edl_bailleur", "honoraires_edl_locataire"):
                setattr(bail, champ, getattr(mandat, champ))
        return bail

    @property
    def meuble(self):
        return self.type != self.Type.NU

    @property
    def duree_mois(self):
        if self.type == self.Type.ETUDIANT:
            return 9
        if self.type == self.Type.MEUBLE:
            return 12
        return self.propriete.bailleur.duree_bail_nu_ans * 12

    @property
    def date_fin(self):
        return ajouter_mois(self.date_effet, self.duree_mois) - dt.timedelta(days=1)

    def completer_echeancier(self, aujourd_hui=None):
        """Crée les échéances manquantes, du début du suivi au mois prochain
        (pour envoyer l'avis d'échéance à l'avance), sans dépasser la fin du
        bail. Le premier et le dernier mois sont calculés au prorata des
        jours. Une échéance déjà créée n'est jamais recalculée."""
        if not self.suivi_loyers_depuis:
            return []
        aujourd_hui = aujourd_hui or timezone.localdate()
        dernier_jour = self.date_fin_effective or ajouter_mois(aujourd_hui.replace(day=1), 2) - dt.timedelta(days=1)
        mois = max(self.suivi_loyers_depuis, self.date_effet).replace(day=1)
        existantes = set(self.echeances.values_list("periode", flat=True))
        creees = []
        while mois <= dernier_jour:
            fin_mois = ajouter_mois(mois, 1) - dt.timedelta(days=1)
            debut, fin = max(mois, self.date_effet), min(fin_mois, dernier_jour if self.date_fin_effective else fin_mois)
            if mois not in existantes and debut <= fin:
                part = Decimal((fin - debut).days + 1) / Decimal(fin_mois.day)
                jour = min(self.jour_paiement, fin_mois.day)
                if self.a_echoir:
                    exigible = max(mois.replace(day=jour), debut)
                else:
                    suivant = ajouter_mois(mois, 1)
                    exigible = suivant.replace(day=min(self.jour_paiement, calendar.monthrange(suivant.year, suivant.month)[1]))
                creees.append(Echeance.objects.create(
                    bail=self, periode=mois, debut=debut, fin=fin, exigible_le=exigible,
                    loyer=(self.loyer * part).quantize(Decimal("0.01")),
                    charges=((self.charges or 0) * part).quantize(Decimal("0.01")),
                ))
            mois = ajouter_mois(mois, 1)
        return creees

    @property
    def mois_depot_max(self):
        """Un mois de loyer hors charges en nu, deux en meublé."""
        return 2 if self.meuble else 1

    @property
    def en_cours(self):
        return self.date_fin_effective is None

    @property
    def signature_retirable(self):
        """Retour de l'entrée à la signature : la signature se retire tant
        que le locataire n'est pas entré dans les lieux, et le bail redevient
        modifiable."""
        return self.signe and self.en_cours and not self.conge_recu_le and self.date_effet > timezone.localdate()

    @property
    def conge_annulable(self):
        """Retour de la sortie à la location : tant que le locataire n'est pas parti."""
        return bool(self.conge_recu_le) and self.en_cours

    @property
    def parties(self):
        return self.noms_locataires

    @property
    def debut(self):
        return self.date_effet

    @property
    def echeance(self):
        if self.date_fin_effective:
            return self.date_fin_effective, ""
        if self.date_depart_prevue:
            return self.date_depart_prevue, "départ prévu"
        return self.date_fin, ""

    @property
    def etat(self):
        if not self.en_cours:
            return "sans_bail", "Terminé"
        if self.conge_recu_le:
            return "preavis", "Préavis"
        if not self.signe:
            return "libre", "À signer"
        if self.alertes:
            return "libre", "À vérifier"
        return "loue", "En cours"

    @property
    def mois_preavis(self):
        """Préavis du locataire : un mois en meublé ou en zone tendue, trois
        mois sinon (art. 15 et 25-8 de la loi du 6 juillet 1989)."""
        return 1 if self.meuble or self.propriete.zone_tendue else 3

    @property
    def noms_locataires(self):
        return ", ".join(str(locataire) for locataire in self.locataires.all())

    @classmethod
    def empechement(cls, propriete, date_effet=None, sauf=None):
        """Raison pour laquelle le logement ne peut pas être loué à la date
        donnée, ou None : il est déjà loué sans congé enregistré, ou le
        locataire en place n'est pas encore parti. Renvoie (bail, message).
        Seuls comptent les baux qui ont pris effet avant."""
        precedents = propriete.baux.exclude(pk=sauf).prefetch_related("locataires").order_by("-date_effet")
        if date_effet:
            precedents = precedents.filter(date_effet__lte=date_effet)
        for bail in precedents:
            qui = bail.noms_locataires or "locataire à renseigner"
            if bail.en_cours and not bail.conge_recu_le:
                return bail, (
                    f"Ce logement est déjà loué ({qui}, bail du {bail.date_effet:%d/%m/%Y}) et aucun congé "
                    "n'est enregistré. Enregistrez d'abord le préavis du locataire sur ce bail, ou sa fin "
                    "effective s'il est parti."
                )
            depart = bail.date_fin_effective or bail.date_depart_prevue
            if date_effet and depart and date_effet <= depart:
                return bail, (
                    f"Le bail précédent ({qui}) {'a pris' if bail.date_fin_effective else 'prend'} fin le "
                    f"{depart:%d/%m/%Y} : le nouveau bail ne peut prendre effet qu'après cette date."
                )
        return None

    @property
    def total_mensuel(self):
        return (self.loyer or 0) + (self.charges or 0)

    @property
    def interdictions(self):
        """Ce que la loi interdit : le bail ne peut être ni préparé, ni
        téléchargé, ni signé tant que ces points ne sont pas corrigés."""
        from documents.generation import montant

        propriete = self.propriete
        interdictions = []
        if propriete.location_interdite:
            interdictions.append(
                "Logement classé G : il ne peut plus faire l'objet d'un nouveau bail depuis le 1er janvier 2025 "
                "(article 6 de la loi du 6 juillet 1989)."
            )
        if not propriete.est_habitation:
            interdictions.append(
                f"Ce bien est un {propriete.get_usage_display().lower()} : le bail d'habitation ne lui "
                "convient pas, et l'application n'a pas encore de modèle pour ce type de bail."
            )
        if self.loyer and self.depot_garantie > self.loyer * self.mois_depot_max:
            interdictions.append(
                f"Dépôt de garantie supérieur à {self.mois_depot_max} mois de loyer hors charges "
                f"({montant(self.loyer * self.mois_depot_max)} €, article 22 de la loi du 6 juillet 1989)."
            )
        return interdictions

    @property
    def alertes(self):
        """Points à corriger avant de faire signer le bail ; les premiers
        (``interdictions``) bloquent la préparation et la signature."""
        propriete = self.propriete
        alertes = list(self.interdictions)
        manquants = propriete.champs_manquants_pour_bail()
        if manquants:
            alertes.append("Fiche du bien incomplète : " + ", ".join(manquants) + ".")
        if self.mandat is None:
            alertes.append("Aucun mandat de gestion n'est rattaché à ce bail.")
        if self.colocation and self.pk and self.locataires.count() < 2:
            alertes.append("Colocation cochée avec un seul locataire : la clause de colocation ne s'appliquera pas.")
        if self.precedent_loyer and self.loyer and self.loyer > self.precedent_loyer:
            if propriete.classe_dpe in ("F", "G"):
                alertes.append(
                    "Logement classé F ou G : le loyer ne peut pas dépasser celui du précédent locataire."
                )
            elif propriete.zone_tendue:
                alertes.append(
                    "Zone tendue : le loyer dépasse celui du précédent locataire. Il ne peut être augmenté "
                    "qu'en application de l'IRL ou dans les cas prévus par le décret d'encadrement "
                    "(travaux, loyer sous-évalué) ; vérifiez avant de signer."
                )
        alertes += controler_honoraires(
            propriete, self.honoraires_bail_bailleur, self.honoraires_bail_locataire,
            self.honoraires_edl_bailleur, self.honoraires_edl_locataire,
        )
        return alertes

    @property
    def liste_garants(self):
        return lignes(self.garants)

    def texte_solidarite(self, nb_locataires):
        texte = ("Les locataires sont tenus solidairement et indivisiblement de l'ensemble des obligations "
                 "découlant du présent contrat, notamment du paiement du loyer, des charges et des réparations "
                 "locatives.")
        if not (self.colocation and nb_locataires > 1):
            return texte + (" Cette solidarité cesse dans les conditions prévues par l'article 8-1 de la loi "
                            "n° 89-462 du 6 juillet 1989.")
        return texte + (" Conformément à l'article 8-1 de la loi n° 89-462 du 6 juillet 1989, la solidarité "
                        "d'un colocataire qui donne congé, et celle de la personne qui s'est portée caution "
                        "pour lui, prennent fin à la date d'effet de son congé lorsqu'un nouveau colocataire "
                        "figure au bail ; à défaut, elles s'éteignent au plus tard six mois après la date "
                        "d'effet du congé.")

    def annexes(self):
        propriete = self.propriete
        liste = []
        if propriete.regime_juridique == Propriete.Regime.COPROPRIETE:
            liste.append(
                "Extrait du règlement de copropriété concernant la destination de l'immeuble, la jouissance "
                "et l'usage des parties privatives et communes, et la quote-part afférente au lot loué"
            )
        liste.append("Diagnostic de performance énergétique")
        if propriete.periode_construction == Propriete.Periode.AVANT_1949:
            liste.append("Constat de risque d'exposition au plomb")
        if propriete.periode_construction in (Propriete.Periode.AVANT_1949, Propriete.Periode.DE_1949_A_1974,
                                         Propriete.Periode.DE_1975_A_1989, ""):
            liste.append("Le cas échéant, état mentionnant la présence ou l'absence d'amiante "
                         "(immeubles dont le permis de construire est antérieur au 1er juillet 1997)")
        liste.append("Le cas échéant, état de l'installation intérieure d'électricité et de gaz "
                     "(installations de plus de quinze ans)")
        liste.append("État des risques et pollutions")
        liste.append("Le cas échéant, diagnostic bruit (zones de bruit des aérodromes)")
        liste.append("Notice d'information relative aux droits et obligations des locataires et des bailleurs")
        liste.append("État des lieux d'entrée" + (", inventaire et état détaillé du mobilier" if self.meuble else ""))
        liste.append("Liste des réparations locatives (décret n° 87-712 du 26 août 1987)")
        return liste

    def contexte_document(self):
        from documents.archives import derniere_version

        version = derniere_version(self) if self.signe else None
        return version.contexte if version else self.contexte_courant()

    def contexte_courant(self):
        from documents.generation import montant

        propriete, bailleur = self.propriete, self.propriete.bailleur
        locataires = list(self.locataires.all())

        def date(valeur):
            return valeur.strftime("%d/%m/%Y") if valeur else ""

        def ou(valeur, defaut="Néant"):
            return valeur or defaut

        if self.meuble:
            intitule = "Logement meublé" + (" loué à un étudiant" if self.type == self.Type.ETUDIANT else "")
            regime = ("Titre Ier bis de la loi n° 89-462 du 6 juillet 1989 · contrat type de l'annexe 2 "
                      "du décret n° 2015-587 du 29 mai 2015")
        else:
            intitule = "Logement nu"
            regime = ("Titre Ier de la loi n° 89-462 du 6 juillet 1989 · contrat type de l'annexe 1 "
                      "du décret n° 2015-587 du 29 mai 2015")
        if self.type == self.Type.ETUDIANT:
            texte_duree = ("Le contrat, consenti à un étudiant, n'est pas reconduit tacitement : il prend fin "
                           "à son terme. Le locataire peut donner congé à tout moment avec un préavis d'un mois.")
        elif self.meuble:
            texte_duree = ("À défaut de congé, le contrat est reconduit tacitement pour une durée d'un an. "
                           "Le locataire peut donner congé à tout moment avec un préavis d'un mois ; le bailleur "
                           "peut donner congé à l'échéance avec un préavis de trois mois, dans les conditions "
                           "de l'article 25-8 de la loi du 6 juillet 1989.")
        else:
            texte_duree = ("À défaut de congé, le contrat est reconduit tacitement pour une durée égale à sa durée "
                           "initiale. Le locataire peut donner congé à tout moment avec un préavis de trois mois, "
                           "réduit à un mois dans les cas prévus par l'article 15 de la loi du 6 juillet 1989 "
                           "(notamment en zone tendue) ; le bailleur peut donner congé à l'échéance avec un "
                           "préavis de six mois.")
        if propriete.depenses_energie_min and propriete.depenses_energie_max:
            energie = f"entre {montant(propriete.depenses_energie_min)} € et {montant(propriete.depenses_energie_max)} € par an"
            if propriete.date_dpe:
                energie += f" (diagnostic du {date(propriete.date_dpe)})"
        else:
            energie = "non communiqué"
        nb_mois = self.mois_depot_max
        from core.models import Organisation

        return {
            "agence": Organisation.courante().contexte_document(),
            "intitule": intitule,
            "regime": regime,
            "bailleur": bailleur.designation,
            "bailleur_adresse": f"{bailleur.adresse}, {bailleur.code_postal} {bailleur.ville}".strip(", "),
            "bailleur_qualite": bailleur.get_type_display().lower(),
            "bailleur_email": bailleur.email,
            "locataires": [
                {
                    "designation": locataire.designation,
                    "naissance": locataire.naissance,
                    "email": locataire.email,
                    "garants": lignes(locataire.garants),
                }
                for locataire in locataires
            ],
            "plusieurs_locataires": len(locataires) > 1,
            "colocation": self.colocation and len(locataires) > 1,
            "texte_solidarite": self.texte_solidarite(len(locataires)),
            "garants": lignes(self.garants),
            "adresse_logement": ", ".join(
                m for m in [propriete.adresse, propriete.complement,
                            f"étage {propriete.etage}" if propriete.etage else "",
                            f"appartement n° {propriete.numero_appartement}" if propriete.numero_appartement else "",
                            f"{propriete.code_postal} {propriete.ville}"] if m
            ),
            "identifiant_fiscal": propriete.identifiant_fiscal,
            "type_habitat": ou(propriete.get_type_habitat_display(), "non précisé"),
            "regime_juridique": ou(propriete.get_regime_juridique_display(), "non précisé"),
            "periode_construction": ou(propriete.get_periode_construction_display(), "non précisée"),
            "surface": montant(propriete.surface),
            "nb_pieces": propriete.nb_pieces or "",
            "equipements": ou(propriete.equipements),
            "chauffage": ou(propriete.get_chauffage_display(), "non précisé"),
            "eau_chaude": ou(propriete.get_eau_chaude_display(), "non précisé"),
            "classe_dpe": propriete.classe_dpe or "non communiquée",
            "destination": "Usage mixte professionnel et d'habitation." if self.usage_mixte
                           else "Usage d'habitation, à titre de résidence principale du locataire.",
            "accessoires": ou(", ".join(m for m in [
                propriete.annexes,
                f"parking n° {propriete.numero_parking}" if propriete.numero_parking else "",
                f"cave ou cellier n° {propriete.numero_cellier}" if propriete.numero_cellier else "",
            ] if m)),
            "parties_communes": ou(propriete.parties_communes),
            "acces_tic": ou(propriete.acces_tic),
            "date_effet": date(self.date_effet),
            "duree": f"{self.duree_mois // 12} an{'s' if self.duree_mois >= 24 else ''}"
                     if self.duree_mois % 12 == 0 else f"{self.duree_mois} mois",
            "date_fin": date(self.date_fin),
            "texte_duree": texte_duree,
            "loyer": montant(self.loyer),
            "evolution_encadree": "oui" if propriete.zone_tendue else "non",
            "loyer_reference_applicable": "oui" if propriete.loyer_reference_majore else "non",
            "precedent_loyer": montant(self.precedent_loyer) if self.precedent_loyer else "",
            "precedent_date_versement": date(self.precedent_date_versement) or "non communiquée",
            "precedent_date_revision": date(self.precedent_date_revision) or "non communiquée",
            "date_revision": self.date_effet.strftime("%d/%m") + " de chaque année",
            "irl_trimestre": self.irl_trimestre,
            "irl_valeur": montant(self.irl_valeur),
            "mode_charges": self.get_mode_charges_display().lower(),
            "libelle_charges": "du forfait" if self.mode_charges == self.ModeCharges.FORFAIT else "des provisions",
            "charges": montant(self.charges),
            "terme": "à échoir" if self.a_echoir else "à terme échu",
            "jour_paiement": "1er" if self.jour_paiement == 1 else str(self.jour_paiement),
            "total_mensuel": montant(self.total_mensuel),
            "depenses_energie": energie,
            "travaux": ou(self.travaux),
            "depot_garantie": montant(self.depot_garantie),
            "texte_depot": (
                f"Le dépôt de garantie ne peut excéder {'deux mois' if nb_mois == 2 else 'un mois'} de loyer "
                "hors charges. Il est restitué dans un délai d'un mois à compter de la remise des clés si "
                "l'état des lieux de sortie est conforme à l'état des lieux d'entrée, de deux mois dans le "
                "cas contraire, déduction faite des sommes restant dues au bailleur."
            ),
            "plafond_bail": montant(PLAFOND_BAIL_ZONE_TENDUE if propriete.zone_tendue else PLAFOND_BAIL_HORS_ZONE_TENDUE),
            "honoraires_bail_bailleur": montant(self.honoraires_bail_bailleur),
            "honoraires_bail_locataire": montant(self.honoraires_bail_locataire),
            "honoraires_edl_bailleur": montant(self.honoraires_edl_bailleur),
            "honoraires_edl_locataire": montant(self.honoraires_edl_locataire),
            "conditions_particulieres": self.conditions_particulieres,
            "annexes": self.annexes(),
            "lieu_signature": self.lieu_signature,
            "date_signature": date(self.date_signature) or "……………………",
            "exemplaires": len(locataires) + 1,
        }

    @property
    def nom_fichier(self):
        noms = " ".join(locataire.nom.upper() for locataire in self.locataires.all())
        return f"Bail {self.propriete.adresse} {noms}".strip()[:120]


class Locataire(models.Model):
    class Civilite(models.TextChoices):
        MONSIEUR = "M.", "Monsieur"
        MADAME = "Mme", "Madame"

    bail = models.ForeignKey(Bail, on_delete=models.CASCADE, related_name="locataires")
    civilite = models.CharField("civilité", max_length=5, choices=Civilite.choices, blank=True)
    nom = models.CharField(max_length=100)
    prenom = models.CharField("prénom", max_length=100)
    date_naissance = models.DateField("date de naissance", null=True, blank=True)
    lieu_naissance = models.CharField("lieu de naissance", max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = TelephoneField("téléphone", blank=True)
    # En colocation, l'acte de caution doit désigner le colocataire garanti.
    garants = models.TextField(
        blank=True, help_text="Nom et adresse de chaque caution de ce locataire, une par ligne."
    )

    # La personne, quelle que soit la ligne par laquelle elle apparaît.
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name="locataires",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["pk"]
        verbose_name = "locataire"

    def save(self, *args, **kwargs):
        from contacts.models import Contact

        self.contact = Contact.rattacher(self)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.prenom} {self.nom.upper()}"

    @property
    def designation(self):
        civilite = self.Civilite(self.civilite).label if self.civilite else ""
        return " ".join(m for m in (civilite, self.nom.upper(), self.prenom) if m)

    @property
    def liste_garants(self):
        return lignes(self.garants)

    @property
    def naissance(self):
        if not self.date_naissance:
            return ""
        texte = f", né(e) le {self.date_naissance:%d/%m/%Y}"
        return texte + (f" à {self.lieu_naissance}" if self.lieu_naissance else "")


MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
        "décembre"]


class Echeance(models.Model):
    """Loyer d'un mois pour un bail suivi : ce qui est dû, pour quand, et si
    c'est payé. Elle donne l'avis d'échéance, puis la quittance une fois le
    paiement pointé. Ce n'est pas de la comptabilité : ni compte du mandant,
    ni reversement, ni paiement partiel."""

    # Un loyer est signalé impayé quelques jours après sa date d'exigibilité.
    TOLERANCE = dt.timedelta(days=5)

    bail = models.ForeignKey(Bail, on_delete=models.CASCADE, related_name="echeances")
    periode = models.DateField("mois")  # premier jour du mois
    debut = models.DateField("du")
    fin = models.DateField("au")
    loyer = _montant(verbose_name="loyer hors charges (€)")
    charges = _montant(verbose_name="charges (€)", default=0)
    exigible_le = models.DateField("exigible le")
    paye_le = models.DateField("payé le", null=True, blank=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-periode"]
        verbose_name = "échéance de loyer"
        constraints = [models.UniqueConstraint(fields=["bail", "periode"], name="echeance_unique_par_mois")]

    def __str__(self):
        return f"Loyer de {self.libelle} · {self.bail.propriete.adresse}"

    def get_absolute_url(self):
        return f"{self.bail.get_absolute_url()}#loyers"

    @property
    def libelle(self):
        return f"{MOIS[self.periode.month - 1]} {self.periode.year}"

    @property
    def total(self):
        return self.loyer + self.charges

    @property
    def partielle(self):
        """Mois incomplet (entrée ou sortie en cours de mois), calculé au prorata."""
        return self.debut != self.periode or self.fin != ajouter_mois(self.periode, 1) - dt.timedelta(days=1)

    @property
    def payee(self):
        return self.paye_le is not None

    @property
    def etat(self):
        """(code de pastille, libellé)."""
        aujourd_hui = timezone.localdate()
        if self.payee:
            return "loue", f"Payé le {self.paye_le:%d/%m/%Y}"
        if self.exigible_le + self.TOLERANCE < aujourd_hui:
            return "libre", "Impayé"
        if self.exigible_le <= aujourd_hui:
            return "preavis", "À encaisser"
        return "sans_bail", "À venir"

    @property
    def impayee(self):
        return self.etat[0] == "libre"

    @property
    def nom_fichier(self):
        noms = " ".join(locataire.nom.upper() for locataire in self.bail.locataires.all())
        return f"{'Quittance' if self.payee else 'Avis d échéance'} {self.libelle} {noms}".strip()[:120]

    def contexte_document(self):
        """Valeurs pour les modèles Word de l'avis d'échéance et de la quittance."""
        from core.models import Organisation
        from documents.generation import montant

        bail, propriete = self.bail, self.bail.propriete

        def date(valeur):
            return valeur.strftime("%d/%m/%Y") if valeur else ""

        return {
            "agence": Organisation.courante().contexte_document(),
            "bailleur": propriete.bailleur.designation,
            "locataires": bail.noms_locataires,
            "adresse": ", ".join(m for m in [propriete.adresse, propriete.complement,
                                              f"{propriete.code_postal} {propriete.ville}"] if m),
            "periode": self.libelle,
            "debut": date(self.debut),
            "fin": date(self.fin),
            "loyer": montant(self.loyer),
            "charges": montant(self.charges),
            "libelle_charges": "Forfait de charges" if bail.mode_charges == "forfait" else "Provision pour charges",
            "total": montant(self.total),
            "exigible_le": date(self.exigible_le),
            "paye_le": date(self.paye_le),
            "date": date(timezone.localdate()),
            "lieu": bail.lieu_signature,
        }
