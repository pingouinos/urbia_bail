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

from contrats.base import Contrat
from proprietes.models import Propriete

# Plafonds des honoraires de location imputables au locataire, en euros TTC
# par m² de surface habitable (décret n° 2014-890 du 1er août 2014).
PLAFOND_BAIL_ZONE_TENDUE = Decimal("10")
PLAFOND_BAIL_HORS_ZONE_TENDUE = Decimal("8")
PLAFOND_ETAT_DES_LIEUX = Decimal("3")

# Information sur le droit de rétractation (code de la consommation, L221-5,
# L221-18 et L221-25), pour un particulier qui signe hors de l'agence ou à distance.
RETRACTATION = [
    "Le mandant dispose d'un délai de quatorze jours à compter de la signature du présent mandat pour se "
    "rétracter, sans avoir à motiver sa décision (articles L221-18 et suivants du code de la consommation).",
    "Pour exercer ce droit, il notifie sa décision au mandataire, à l'adresse indiquée en tête du présent mandat, "
    "par une déclaration dénuée d'ambiguïté (lettre recommandée avec avis de réception ou courrier électronique). "
    "Il peut utiliser le formulaire de rétractation ci-dessous, sans que ce soit obligatoire.",
    "En cas de rétractation, le mandataire rembourse tous les paiements reçus au plus tard quatorze jours après "
    "en avoir été informé.",
    "Si le mandant demande expressément que l'exécution du mandat commence avant la fin du délai de "
    "rétractation, il reste redevable, s'il se rétracte, d'un montant proportionnel aux prestations fournies "
    "jusqu'à la communication de sa rétractation (article L221-25).",
    "☐ Le mandant demande expressément que l'exécution du mandat commence avant la fin du délai de "
    "rétractation.",
]
# Formulaire type de l'annexe à l'article R221-1 du code de la consommation.
FORMULAIRE_RETRACTATION = [
    "FORMULAIRE DE RÉTRACTATION",
    "(Veuillez compléter et renvoyer le présent formulaire uniquement si vous souhaitez vous rétracter du contrat.)",
    "À l'attention du mandataire désigné en tête du présent mandat :",
    "Je/nous (*) vous notifie/notifions (*) par la présente ma/notre (*) rétractation du contrat pour la "
    "prestation de services ci-dessous : mandat de gestion n° ……",
    "Conclu le : ……",
    "Nom du (des) consommateur(s) : ……",
    "Adresse du (des) consommateur(s) : ……",
    "Signature du (des) consommateur(s) (uniquement en cas de notification du présent formulaire sur papier) :",
    "Date : ……",
    "(*) Rayez la mention inutile.",
]

NOMBRES = {1: "un", 2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six"}


def ranger_mandat_signe(mandat, nom):
    from core.fichiers import nom_aleatoire

    return f"mandats/{nom_aleatoire(nom)}"


def plafonds_locataire(propriete):
    """Plafonds (bail, état des lieux) de la part locataire, ou None si le
    bien n'y est pas soumis ou si sa surface est inconnue."""
    if not propriete.est_habitation or not propriete.surface:
        return None
    par_m2 = PLAFOND_BAIL_ZONE_TENDUE if propriete.zone_tendue else PLAFOND_BAIL_HORS_ZONE_TENDUE
    return propriete.surface * par_m2, propriete.surface * PLAFOND_ETAT_DES_LIEUX


def controler_honoraires(propriete, bail_bailleur, bail_locataire, edl_bailleur, edl_locataire):
    """Points contraires à l'article 5 I de la loi du 6 juillet 1989 : la part
    du locataire ne peut excéder celle du bailleur ni le plafond par m² de
    surface habitable."""
    from documents.generation import montant

    if not propriete.est_habitation:
        return []
    alertes = []
    prestations = [
        ("visite, dossier et bail", bail_locataire, bail_bailleur),
        ("état des lieux", edl_locataire, edl_bailleur),
    ]
    for libelle, locataire, bailleur in prestations:
        if locataire > bailleur:
            alertes.append(
                f"Honoraires de {libelle} : la part du locataire ({montant(locataire)} €) dépasse "
                f"celle du bailleur ({montant(bailleur)} €), ce que la loi interdit."
            )
    plafonds = plafonds_locataire(propriete)
    if plafonds is None:
        if bail_locataire or edl_locataire:
            alertes.append("Surface habitable du bien inconnue : plafonds non vérifiés.")
        return alertes
    for (libelle, locataire, _), plafond in zip(prestations, plafonds):
        if locataire > plafond:
            alertes.append(
                f"Honoraires de {libelle} : la part du locataire ({montant(locataire)} €) dépasse "
                f"le plafond légal de {montant(plafond)} € pour {montant(propriete.surface)} m²."
            )
    return alertes


def _montant(**options):
    return models.DecimalField(max_digits=9, decimal_places=2, **options)


class Mandat(Contrat):
    TYPE = "Mandat"

    class BaseGestion(models.TextChoices):
        ENCAISSEMENTS = "encaissements", "Encaissements mensuels"
        HORS_CHARGES = "hors_charges", "Encaissements hors charges"
        CHARGES_COMPRISES = "charges_comprises", "Encaissements charges comprises"

    class Conclusion(models.TextChoices):
        AGENCE = "agence", "Mandant particulier, signature dans les locaux de l'agence"
        HORS_ETABLISSEMENT = "hors_etablissement", "Mandant particulier, signature chez lui ou ailleurs qu'à l'agence"
        DISTANCE = "distance", "Mandant particulier, signature à distance (courrier, e-mail, signature électronique)"
        PROFESSIONNEL = "professionnel", "Mandant professionnel (société, SCI non familiale)"

    numero = models.PositiveIntegerField(
        "numéro au registre", unique=True,
        error_messages={"unique": "Ce numéro est déjà porté par un autre mandat."},
    )
    propriete = models.ForeignKey(
        Propriete, on_delete=models.PROTECT, related_name="mandats", verbose_name="propriété",
    )

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

    # Signature : un particulier qui signe hors de l'agence peut se rétracter
    # pendant 14 jours (code de la consommation, L221-18).
    conclusion = models.CharField(
        "mandant et lieu de signature", max_length=20, choices=Conclusion.choices, blank=True,
        help_text="Un particulier qui signe ailleurs qu'à l'agence ou à distance dispose de 14 jours pour se "
                  "rétracter : le mandat l'en informe et joint le formulaire.",
    )
    lieu_signature = models.CharField("lieu de signature", max_length=100, default="Toulouse")

    fichier_signe = models.FileField("mandat signé (scan)", upload_to=ranger_mandat_signe, blank=True)
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
        # Le numéro se saisit d'après le registre papier. Sans numéro (reprise
        # de données, traitement automatique), on prend le suivant ; on
        # réessaie si un autre mandat a été créé au même instant.
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
    def depuis_propriete(cls, propriete):
        """Mandat prérempli à partir des fiches du bien et du bailleur."""
        from core.models import Organisation

        bailleur = propriete.bailleur
        loyer = (propriete.dernier_loyer or 0) + (propriete.charges or 0)
        # Honoraires de location proposés au plafond légal par m², selon la
        # zone ; la part du bailleur égale celle du locataire.
        honoraires_bail, honoraires_edl = (
            plafond.quantize(Decimal("0.01")) for plafond in plafonds_locataire(propriete) or (Decimal("0"), Decimal("0"))
        )
        return cls(
            propriete=propriete,
            taux_gestion=Organisation.courante().taux_gestion,
            honoraires_bail_bailleur=honoraires_bail,
            honoraires_bail_locataire=honoraires_bail,
            honoraires_edl_bailleur=honoraires_edl,
            honoraires_edl_locataire=honoraires_edl,
            mandant=bailleur.designation,
            mandant_adresse=bailleur.adresse,
            mandant_code_postal_ville=f"{bailleur.code_postal} {bailleur.ville}".strip(),
            designation=propriete.designation,
            adresse_bien=f"{propriete.adresse} {propriete.code_postal} {propriete.ville.upper()}",
            loyer_cc=loyer or None,
            charges=propriete.charges or 0,
            conclusion=cls.Conclusion.PROFESSIONNEL if bailleur.type == bailleur.Type.PERSONNE_MORALE else "",
        )

    @property
    def en_cours(self):
        return self.date_fin is None

    @property
    def signature_retirable(self):
        """‹ Du dossier à la signature : tant que le mandat n'est pas en
        gestion (dossier incomplet, aucun bail signé sous ce mandat)."""
        return (
            self.signe and self.en_cours and bool(self.pieces_manquantes)
            and not self.baux.filter(date_signature__isnull=False).exists()
        )

    @property
    def parties(self):
        return self.mandant

    @property
    def debut(self):
        return self.date_signature

    @property
    def echeance(self):
        return self.date_fin, ""

    @property
    def etat(self):
        if not self.en_cours:
            return "sans_bail", "Terminé"
        if not self.signe:
            return "libre", "À signer"
        if self.alertes:
            return "libre", "À vérifier"
        return "loue", "En cours"

    @property
    def dossier(self):
        """Pièces que le mandant fournit avec le mandat, et où elles en sont.
        Celles du bailleur (identité, RIB) valent pour tous ses mandats."""
        from proprietes.models import Piece

        propriete, bailleur, T = self.propriete, self.propriete.bailleur, Piece.Type
        attendues = [
            (T.IDENTITE, bailleur, True, "du représentant, pour une société" if bailleur.societe else ""),
            (T.RIB, bailleur, True, ""),
            # Une société : son existence et le pouvoir de celui qui signe pour elle.
            *([(T.KBIS, bailleur, True, "extrait RNE, gratuit sur annuaire-entreprises.data.gouv.fr, ou Kbis"),
               (T.STATUTS, bailleur, False, "pour vérifier les pouvoirs du gérant")] if bailleur.societe else []),
            (T.TAXE_FONCIERE, propriete, True, ""),
            (T.TITRE, propriete, True, ""),
            (T.CHARGES, propriete, propriete.regime_juridique != propriete.Regime.MONOPROPRIETE, "si le logement est en copropriété"),
            (T.ASSURANCE, propriete, True, ""),
            (T.BAIL, propriete, False, "si le logement est déjà loué"),
            (T.ETAT_DES_LIEUX, propriete, False, "si le logement est déjà loué"),
            (T.DPE, propriete, True, ""),
            (T.ELECTRICITE, propriete, True, ""),
            (T.SURFACE, propriete, True, ""),
        ]
        deposees = {}
        for piece in [*bailleur.pieces.all(), *propriete.pieces.all()]:
            # La plus récente de chaque type, selon l'ordre du modèle.
            deposees.setdefault((piece.type, piece.bailleur_id is not None), piece)
        return [
            {
                "type": type_, "libelle": type_.label, "du_bailleur": cible is bailleur, "cible": cible,
                "obligatoire": obligatoire, "note": note, "piece": deposees.get((type_, cible is bailleur)),
                "avec_date": type_ in Piece.VALIDITE or type_ == T.KBIS,
            }
            for type_, cible, obligatoire, note in attendues
        ]

    @property
    def pieces_manquantes(self):
        return [
            ligne["libelle"] for ligne in self.dossier
            if ligne["obligatoire"] and (ligne["piece"] is None or ligne["piece"].perime)
        ]

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

    @property
    def retractation_possible(self):
        return self.conclusion in (self.Conclusion.HORS_ETABLISSEMENT, self.Conclusion.DISTANCE)

    @property
    def interdictions(self):
        """Ce qui empêche de préparer ou de signer le mandat : mentions légales
        de l'agence (loi Hoguet, code de la consommation) et lieu de signature."""
        from core.models import Organisation

        agence = Organisation.courante()
        interdictions = []
        if agence.manquants:
            interdictions.append(
                "Mentions légales de l'agence à compléter dans Administration › Agence : "
                + ", ".join(agence.manquants) + "."
            )
        if self.assurance_loyers_impayes and agence.manquants_assurance:
            interdictions.append(
                "Assurance loyers impayés proposée : renseigner dans Administration › Agence "
                + ", ".join(agence.manquants_assurance) + "."
            )
        if not self.conclusion:
            interdictions.append(
                "Indiquer qui est le mandant et où il signe : un particulier qui signe hors de l'agence ou à "
                "distance a 14 jours pour se rétracter, et le mandat doit l'en informer."
            )
        return interdictions

    @property
    def alertes(self):
        # Un mandat signé n'a plus à être préparé : seuls les honoraires restent à vérifier.
        return ([] if self.signe else self.interdictions) + controler_honoraires(
            self.propriete, self.honoraires_bail_bailleur, self.honoraires_bail_locataire,
            self.honoraires_edl_bailleur, self.honoraires_edl_locataire,
        )

    def contexte_document(self):
        from documents.archives import derniere_version

        version = derniere_version(self) if self.signe else None
        return version.contexte if version else self.contexte_courant()

    def contexte_courant(self):
        """Valeurs à placer dans le modèle Word du mandat."""
        from documents.generation import montant

        base = {
            self.BaseGestion.ENCAISSEMENTS: "",
            self.BaseGestion.HORS_CHARGES: " (hors charges)",
            self.BaseGestion.CHARGES_COMPRISES: " charges comprises",
        }[self.base_gestion]
        from core.models import Organisation

        return {
            "agence": Organisation.courante().contexte_document(),
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
            "lieu_signature": self.lieu_signature,
            "retractation": RETRACTATION if self.retractation_possible else [],
            "formulaire_retractation": FORMULAIRE_RETRACTATION if self.retractation_possible else [],
        }

    @property
    def nom_fichier(self):
        return f"Mandat {self.numero} - {self.mandant}"[:120]
