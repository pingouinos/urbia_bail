"""Le mandat, le bail et la mise en location vus comme des processus à étapes.

Comme la liste « À faire », rien n'est saisi : l'étape atteinte se déduit des
fiches (date de signature, pièces déposées, congé reçu…). La frise d'un
dossier est donc toujours à jour, sur le tableau de bord comme sur sa fiche.

Chaque étape a un état :
- « faite » : terminée ;
- « courante » : c'est là que le dossier attend ;
- « a_venir » : pas encore atteinte ;
- « alerte » : en retard, ou manquante alors que la suite a avancé ;
- « sautee » : n'a pas eu lieu (bail terminé sans préavis enregistré).
"""

from dataclasses import dataclass, field

from django.urls import reverse
from django.utils import timezone

FAITE, COURANTE, A_VENIR, ALERTE, SAUTEE = "faite", "courante", "a_venir", "alerte", "sautee"

ETAPES_LOCATION = ["Mandat", "Mise en location", "Candidatures", "Bail", "Signature", "Entrée dans les lieux"]
ETAPES_MANDAT = ["Rédaction", "Signature", "Dossier du mandant", "Gestion", "Fin du mandat"]
ETAPES_BAIL = ["Rédaction", "Signature", "Entrée dans les lieux", "Location en cours", "Préavis", "Sortie"]


@dataclass
class Etape:
    libelle: str
    etat: str = A_VENIR
    date: object = None
    detail: str = ""
    lien: str = ""
    prevue: bool = False  # la date est attendue, pas encore passée

    @property
    def faite(self):
        return self.etat in (FAITE, SAUTEE)

    @property
    def etat_libelle(self):
        """Pour les lecteurs d'écran : l'état que la pastille montre."""
        return {FAITE: "terminée", COURANTE: "en cours", A_VENIR: "à venir", ALERTE: "en retard ou manquante",
                SAUTEE: "sans objet"}[self.etat]


@dataclass
class Processus:
    nom: str
    etapes: list = field(default_factory=list)
    #: Numéro (à partir de 1) de l'étape où le dossier attend ; nombre
    #: d'étapes + 1 quand tout est fait.
    rang: int = 1
    termine: bool = False

    @property
    def courante(self):
        return self.etapes[self.rang - 1] if self.rang <= len(self.etapes) else None

    @property
    def avancement(self):
        """Pourcentage de la frise parcouru."""
        if len(self.etapes) < 2:
            return 100
        return round(100 * (min(self.rang, len(self.etapes)) - 1) / (len(self.etapes) - 1))

    @property
    def en_alerte(self):
        return any(etape.etat == ALERTE for etape in self.etapes)


def _marquer(etapes, rang):
    """États par défaut : faites avant `rang`, courante à `rang`, à venir après."""
    for numero, etape in enumerate(etapes, 1):
        if etape.etat == A_VENIR:
            etape.etat = FAITE if numero < rang else COURANTE if numero == rang else A_VENIR


def _date_redaction(contrat):
    """Date de création de la fiche, sauf pour un contrat signé avant d'être
    saisi dans l'application : elle ne dirait rien de sa rédaction."""
    cree = timezone.localdate(contrat.cree_le) if contrat.cree_le else None
    if cree and contrat.date_signature and contrat.date_signature < cree:
        return None
    return cree


def _pluriel(nombre, mot):
    return f"{nombre} {mot}{'s' if nombre > 1 else ''}"


def processus_location(propriete, mandat=None, a_etudier=0, retenue=None, bail=None):
    """Mise en location d'un logement : du mandat à l'entrée du locataire.
    `bail` est le nouveau bail, signé ou non ; `retenue` la candidature
    retenue qui n'a pas encore de bail."""
    if bail:
        rang = 6 if bail.date_signature else 5
    elif retenue:
        rang = 4
    elif propriete.a_louer or a_etudier:
        rang = 3
    elif mandat:
        rang = 2
    else:
        rang = 1
    etapes = [
        Etape("Mandat", detail=f"n° {mandat.numero}" if mandat else "à créer",
              lien=mandat.get_absolute_url() if mandat else f"{reverse('mandats:creer')}?propriete={propriete.pk}",
              date=mandat.date_signature if mandat else None),
        Etape("Mise en location", date=propriete.mise_en_location_le,
              detail=f"disponible le {propriete.disponible_le:%d/%m/%Y}" if propriete.disponible_le else "",
              lien=reverse("proprietes:mettre_en_location", args=[propriete.pk])),
        Etape("Candidatures", detail=f"{a_etudier} à étudier" if a_etudier else ("aucune pour l'instant" if rang == 3 else ""),
              lien=f"{reverse('candidatures:liste')}?propriete={propriete.pk}"),
        Etape("Bail", detail=(retenue.noms or "candidat retenu") if retenue else (bail.noms_locataires if bail else ""),
              lien=bail.get_absolute_url() if bail else (
                  f"{reverse('contrats:creer')}?candidature={retenue.pk}" if retenue else "")),
        Etape("Signature", date=bail.date_signature if bail else None,
              lien=f"{bail.get_absolute_url()}#signature" if bail else ""),
        Etape("Entrée dans les lieux", date=bail.date_effet if bail else None,
              prevue=bool(bail) and bail.date_effet > timezone.localdate()),
    ]
    if not mandat and rang > 1:
        # La location avance sans mandat de gestion : à régulariser.
        etapes[0].etat = ALERTE
    _marquer(etapes, rang)
    return Processus("Mise en location", etapes, rang)


def processus_mandat(mandat, manquantes=None):
    """Mandat de gestion : rédaction, signature, dossier de pièces, gestion,
    fin. `manquantes` évite de recalculer le dossier quand on l'a déjà."""
    if manquantes is None:
        manquantes = mandat.pieces_manquantes
    url = mandat.get_absolute_url()
    etapes = [
        Etape("Rédaction", date=_date_redaction(mandat), detail=f"n° {mandat.numero}", lien=url),
        Etape("Signature", date=mandat.date_signature, lien=f"{url}#signature"),
        Etape("Dossier du mandant", detail=_pluriel(len(manquantes), "pièce") + " à fournir" if manquantes else "complet",
              lien=f"{url}#dossier"),
        Etape("Gestion"),
        Etape("Fin du mandat", date=mandat.date_fin),
    ]
    if mandat.date_fin:
        rang = 6
        # Terminé sans signature ou dossier enregistrés : rien à relancer.
        if not mandat.signe:
            etapes[1].etat = SAUTEE
        if manquantes:
            etapes[2].etat = SAUTEE
    elif not mandat.signe:
        rang = 2
    elif manquantes:
        rang = 3
    else:
        rang = 4
    _marquer(etapes, rang)
    return Processus("Mandat", etapes, rang, termine=bool(mandat.date_fin))


def processus_bail(bail, alertes=None):
    """Bail : rédaction, signature, entrée, location, préavis, sortie.
    Les points à vérifier ne sont calculés que pour un bail non signé."""
    aujourd_hui = timezone.localdate()
    url = bail.get_absolute_url()
    if alertes is None:
        alertes = [] if bail.signe else bail.alertes
    depart = bail.date_depart_prevue
    etapes = [
        Etape("Rédaction", date=_date_redaction(bail),
              detail=_pluriel(len(alertes), "point") + " à vérifier" if alertes else "", lien=url),
        Etape("Signature", date=bail.date_signature,
              detail=("scan déposé" if bail.fichier_signe else "scan à déposer") if bail.signe else "",
              lien=url if bail.signe else reverse("contrats:modifier", args=[bail.pk])),
        Etape("Entrée dans les lieux", date=bail.date_effet, prevue=bail.date_effet > aujourd_hui),
        Etape("Location en cours", detail=f"{bail.total_mensuel} € par mois".replace(".", ","), lien=f"{url}#loyers"),
        Etape("Préavis", date=bail.conge_recu_le,
              detail=("congé du bailleur" if bail.conge_origine == "bailleur" else "congé du locataire") if bail.conge_recu_le else ""),
        Etape("Sortie", date=bail.date_fin_effective or depart or bail.date_fin,
              prevue=not bail.date_fin_effective, detail="" if bail.date_fin_effective or depart else "terme du bail",
              lien=reverse("contrats:fin", args=[bail.pk]) if not bail.date_fin_effective else ""),
    ]
    if bail.date_fin_effective:
        rang = 7
        if not bail.conge_recu_le:
            etapes[4].etat = SAUTEE
    elif bail.conge_recu_le:
        rang = 6 if depart and depart < aujourd_hui else 5
    elif not bail.signe:
        rang = 1 if alertes else 2
    elif bail.date_effet > aujourd_hui:
        rang = 3
    else:
        rang = 4
    if not bail.date_fin_effective and depart and depart < aujourd_hui:
        # Départ annoncé passé et non confirmé.
        etapes[5].etat = ALERTE
    _marquer(etapes, rang)
    return Processus("Bail", etapes, rang, termine=bool(bail.date_fin_effective))

