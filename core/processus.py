"""Le mandat et la location vus comme des processus à étapes. La location
suit un locataire de la mise en location du logement à sa sortie : une seule
frise, la même sur le tableau de bord, la fiche du bail et celle du logement.

Comme la liste « À faire », rien n'est saisi : l'étape atteinte se déduit des
fiches (date de signature, pièces déposées, congé reçu…). La frise d'un
dossier est donc toujours à jour, sur le tableau de bord comme sur sa fiche.

Chaque étape a un état :
- « faite » : terminée ;
- « courante » : c'est là que le dossier attend ;
- « a_venir » : pas encore atteinte ;
- « alerte » : en retard, ou manquante alors que la suite a avancé ;
- « sautee » : n'a pas eu lieu (bail fait sans candidature dans l'application,
  mandat terminé sans signature enregistrée).
"""

from dataclasses import dataclass, field

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils import timezone

FAITE, COURANTE, A_VENIR, ALERTE, SAUTEE = "faite", "courante", "a_venir", "alerte", "sautee"

# Une seule frise par locataire, de la mise en location du logement à sa
# sortie. Le mandat n'en fait pas partie : il couvre plusieurs locataires
# successifs et a sa propre frise.
ETAPES_LOCATION = ["Mise en location", "Candidatures", "Bail", "Signature", "Entrée dans les lieux", "Location en cours", "Sortie"]
ETAPES_MANDAT = ["Rédaction", "Signature", "Dossier du mandant", "Gestion", "Fin du mandat"]


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
    #: La location avance alors que le logement n'a pas de mandat de gestion.
    sans_mandat: bool = False

    @property
    def courante(self):
        return self.etapes[self.rang - 1] if self.rang <= len(self.etapes) else None

    @property
    def avancement(self):
        """Pourcentage de la frise parcouru."""
        if len(self.etapes) < 2:
            return 100
        return round(100 * (min(self.rang, len(self.etapes)) - 1) / (len(self.etapes) - 1))


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


def processus_location(propriete, mandat=None, a_etudier=0, retenue=None, bail=None, alertes=None):
    """La location d'un logement à un locataire, de la mise en location à la
    sortie. Avant le bail : `retenue` est la candidature retenue et
    `a_etudier` le nombre de candidatures en attente. Ensuite, `bail` est le
    bail de ce locataire ; `alertes` ses points à vérifier, calculés pour un
    bail non signé si on ne les donne pas. `mandat` ne sert qu'à signaler une
    location qui avance sans mandat de gestion."""
    aujourd_hui = timezone.localdate()
    if bail and alertes is None:
        alertes = [] if bail.signe else bail.alertes
    alertes = alertes or []
    depart = bail.date_depart_prevue if bail else None
    if not bail:
        rang = 3 if retenue else 2 if propriete.a_louer or a_etudier else 1
    elif bail.termine:
        rang = 8
    elif bail.date_fin_effective or bail.conge_recu_le:
        rang = 7
    elif not bail.signe:
        rang = 3 if alertes else 4
    elif bail.edl_entree_le:
        rang = 6
    elif bail.edl_entree_attendu:
        # L'entrée attend son état des lieux, même la date d'effet passée.
        rang = 5
    else:
        rang = 5 if bail.date_effet > aujourd_hui else 6

    candidature = _candidature(bail) if bail else None
    # La mise en location de ce locataire : la date du logement ne vaut que si
    # elle précède le bail (sinon elle concerne déjà le suivant).
    cree = timezone.localdate(bail.cree_le) if bail and bail.cree_le else None
    mise_en_location = propriete.mise_en_location_le
    if bail and mise_en_location and cree and mise_en_location > cree:
        mise_en_location = None
    etapes = [
        Etape("Mise en location", date=mise_en_location,
              detail=f"disponible le {propriete.disponible_le:%d/%m/%Y}" if propriete.disponible_le and not bail else "",
              lien=reverse("proprietes:mettre_en_location", args=[propriete.pk])),
        Etape("Candidatures",
              detail=(candidature.noms if candidature else "") if bail else (
                  f"{a_etudier} à étudier" if a_etudier else ("aucune pour l'instant" if rang == 2 else "")),
              lien=f"{reverse('candidatures:liste')}?propriete={propriete.pk}"),
        Etape("Bail", date=_date_redaction(bail) if bail else None,
              detail=_pluriel(len(alertes), "point") + " à vérifier" if alertes else (
                  bail.noms_locataires if bail else _retenue(retenue) if retenue else ""),
              lien=(f"{retenue.get_absolute_url()}#lien" if retenue.lien_a_envoyer
                    else f"{reverse('contrats:creer')}?candidature={retenue.pk}") if retenue else ""),
        Etape("Signature", date=bail.date_signature if bail else None,
              detail=("scan déposé" if bail.fichier_signe else "scan à déposer") if bail and bail.signe else ""),
        Etape("Entrée dans les lieux", date=(bail.edl_entree_le or bail.date_effet) if bail else None,
              prevue=bool(bail) and not bail.edl_entree_le and bail.date_effet > aujourd_hui,
              detail="état des lieux à enregistrer" if bail and bail.edl_entree_attendu else ""),
        Etape("Location en cours", detail=f"{bail.total_actuel} € par mois".replace(".", ",") if bail else ""),
        Etape("Sortie", date=_date_sortie(bail) if bail else None,
              prevue=bool(bail) and (not bail.date_fin_effective or bail.restitution_attendue),
              detail=_detail_sortie(bail) if bail else ""),
    ]
    if bail:
        # Depuis un bail, chaque point de la frise ouvre son étape sur la page
        # du bail.
        page = bail.get_absolute_url()
        for numero, etape in enumerate(etapes, 1):
            etape.lien = f"{page}?etape={numero}#etape-{numero}"
    if bail and not candidature:
        # Bail fait sans passer par une candidature (locataire trouvé
        # ailleurs, bail repris à la mise en service) : rien à montrer avant.
        etapes[1].etat = SAUTEE
        if not mise_en_location:
            etapes[0].etat = SAUTEE
    if bail and not bail.date_fin_effective and depart and depart < aujourd_hui:
        # Départ annoncé passé et non confirmé.
        etapes[6].etat = ALERTE
    if bail and bail.edl_entree_attendu and bail.date_effet < aujourd_hui and rang == 5:
        # Locataire entré sans état des lieux enregistré.
        etapes[4].etat = ALERTE
    if bail and bail.restitution_attendue and bail.restitution_avant < aujourd_hui:
        # Dépôt de garantie rendu en retard : la majoration légale court.
        etapes[6].etat = ALERTE
    _marquer(etapes, rang)
    return Processus("Location", etapes, rang, termine=bool(bail and bail.termine),
                     sans_mandat=not mandat and rang > 1)


def processus_bail(bail, alertes=None):
    """La frise vue depuis un bail : celle de la location à son locataire."""
    return processus_location(bail.propriete, mandat=bail.mandat_id, bail=bail, alertes=alertes)


def _retenue(candidature):
    """« Lina ROUX · lien à envoyer » : le candidat retenu complète son
    dossier par son lien avant la rédaction du bail."""
    noms = candidature.noms or "candidat retenu"
    return f"{noms} · lien à envoyer" if candidature.lien_a_envoyer else noms


def _candidature(bail):
    try:
        return bail.candidature
    except ObjectDoesNotExist:
        return None


def _date_sortie(bail):
    """La remise des clés, la fin du préavis ou le terme du bail ; après les
    clés, la date limite de restitution du dépôt."""
    if bail.restitution_attendue:
        return bail.restitution_avant
    return bail.date_fin_effective or bail.date_depart_prevue or bail.date_fin


def _detail_sortie(bail):
    if bail.restitution_attendue:
        return "dépôt à restituer"
    if bail.conge_recu_le and not bail.date_fin_effective:
        return "état des lieux de sortie" if bail.date_depart_prevue and bail.date_depart_prevue <= timezone.localdate() else _conge(bail)
    return _conge(bail)


ETAPES_SORTIE = ["Congé", "État des lieux de sortie", "Restitution du dépôt"]


def sous_etapes_sortie(bail):
    """La petite frise de la sortie : congé, état des lieux de sortie,
    restitution du dépôt. Une restitution sans dépôt est sans objet."""
    rang = bail.sous_etape_sortie
    etapes = [
        Etape("Congé", date=bail.conge_recu_le,
              detail="du bailleur" if bail.conge_origine == "bailleur" else "du locataire" if bail.conge_recu_le else ""),
        Etape("État des lieux de sortie", date=bail.date_fin_effective or bail.date_depart_prevue,
              prevue=not bail.date_fin_effective,
              detail=("conforme" if bail.edl_sortie_conforme else "dégradations" if bail.edl_sortie_conforme is False
                      else "") if bail.date_fin_effective else "départ prévu" if bail.date_depart_prevue else ""),
        Etape("Restitution du dépôt", date=bail.depot_restitue_le or bail.restitution_avant,
              prevue=not bail.depot_restitue_le,
              detail=f"{bail.depot_garantie} €".replace(".", ",") if bail.depot_garantie else ""),
    ]
    if not bail.depot_garantie or (bail.termine and not bail.depot_restitue_le):
        etapes[2].etat = SAUTEE
        etapes[2].date = None
    if bail.restitution_attendue and bail.restitution_avant < timezone.localdate():
        etapes[2].etat = ALERTE
    _marquer(etapes, rang)
    return etapes


def _conge(bail):
    """« congé du locataire reçu le 05/01/2027 », ou le terme du bail."""
    if bail.conge_recu_le:
        qui = "du bailleur" if bail.conge_origine == "bailleur" else "du locataire"
        return f"congé {qui} reçu le {bail.conge_recu_le:%d/%m/%Y}"
    return "" if bail.date_fin_effective else "terme du bail"


def processus_mandat(mandat, manquantes=None):
    """Mandat de gestion : rédaction, signature, dossier de pièces, gestion,
    fin. `manquantes` évite de recalculer le dossier quand on l'a déjà."""
    if manquantes is None:
        manquantes = mandat.pieces_manquantes
    etapes = [
        Etape("Rédaction", date=_date_redaction(mandat), detail=f"n° {mandat.numero}"),
        Etape("Signature", date=mandat.date_signature),
        Etape("Dossier du mandant", detail=_pluriel(len(manquantes), "pièce") + " à fournir" if manquantes else "complet"),
        Etape("Gestion"),
        Etape("Fin du mandat", date=mandat.date_fin),
    ]
    # Chaque point de la frise ouvre sa section sur la page du mandat.
    page = mandat.get_absolute_url()
    for numero, etape in enumerate(etapes, 1):
        etape.lien = f"{page}?etape={numero}#etape-{numero}"
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


# Pastille d'une étape sur la page d'un bail ou d'un mandat.
LIBELLES_ETATS = {FAITE: "Fait", SAUTEE: "Sans objet", COURANTE: "En cours", ALERTE: "À vérifier", A_VENIR: "À venir"}


def sections_etapes(processus, ouverte, ouvrables, verrouillees=(), corrigeables=()):
    """Une section par étape, pour la page d'un bail ou d'un mandat, façon
    PLM : l'étape ouverte avec ses champs, les étapes atteintes grisées et en
    lecture seule, les suivantes à venir. Une étape atteinte qui se rouvre
    (`ouvrables`) porte un crayon ; une étape signée porte un cadenas
    (`verrouillees`), et « Corriger » pour un administrateur (`corrigeables`).
    `ouverte` à None : rien d'ouvert, tout est grisé."""
    sections = []
    for numero, etape in enumerate(processus.etapes, 1):
        if numero == ouverte:
            mode, etat = "ouverte", ALERTE if etape.etat == ALERTE else COURANTE
            libelle = LIBELLES_ETATS[etat] if numero == processus.rang else "Modification"
        else:
            mode, etat = ("grisee", etape.etat) if numero <= processus.rang else ("a-venir", A_VENIR)
            libelle = LIBELLES_ETATS[etat]
        grisee = mode == "grisee" and etape.etat != SAUTEE
        sections.append({
            "numero": numero, "etape": etape, "mode": mode, "etat": etat, "libelle_etat": libelle,
            "crayon": grisee and numero in ouvrables,
            "verrou": grisee and numero in verrouillees and numero not in ouvrables,
            "corriger": mode != "ouverte" and numero in corrigeables,
        })
    return sections


def navigation(processus, ouverte, ouvrables):
    """Les flèches de la frise : ‹ rouvre l'étape précédente si elle se
    rouvre encore (une étape « sans objet » est sautée), › revient vers
    l'étape en cours. Chacune : {numero, libelle}, ou None (flèche grisée)."""
    nombre = len(processus.etapes)
    position = ouverte or nombre + 1
    precedente = next((n for n in range(position - 1, 0, -1) if processus.etapes[n - 1].etat != SAUTEE), None)
    suivante = next((n for n in range(position + 1, min(processus.rang, nombre) + 1) if n in ouvrables), None)
    precedente = precedente if precedente in ouvrables else None
    return tuple(
        {"numero": numero, "libelle": processus.etapes[numero - 1].libelle} if numero else None
        for numero in (precedente, suivante)
    )
