"""Actions en attente, déduites de l'état des fiches.

Rien n'est saisi ni coché : une tâche apparaît quand une fiche demande une
action, et disparaît quand l'action est faite. La liste est donc toujours à
jour, pour tous les collaborateurs.
"""

import datetime as dt
from dataclasses import dataclass

from django.urls import reverse
from django.utils import timezone

from candidatures.models import Candidature
from contrats.models import Bail
from mandats.models import Mandat
from proprietes.models import Bailleur, Piece, Propriete

from .processus import processus_mandat

# Rubriques, dans l'ordre d'affichage.
RUBRIQUES = ["Mises en location", "Loyers", "Mandats", "Candidatures", "Baux", "Documents", "Fiches à compléter"]
# Un diagnostic est signalé un peu avant sa fin de validité.
PREAVIS_VALIDITE = dt.timedelta(days=60)


@dataclass
class Tache:
    rubrique: str
    titre: str
    sujet: str  # la propriété ou la personne concernée
    lien: str
    action: str
    urgent: bool = False
    detail: str = ""
    processus: object = None  # core.processus.Processus : l'étape où attend le dossier


def de(nom):
    """« de Jean », « d'Inès » : le complément de nom avec élision."""
    return f"d'{nom}" if nom[:1].upper() in "AEIOUYÀÂÉÈÊËÎÏÔÛ" else f"de {nom}"


def _nb_pieces(nombre):
    return f"{nombre} pièce{'s' if nombre > 1 else ''}"


def _adresse(propriete):
    return f"{propriete.adresse}{', ' + propriete.complement if propriete.complement else ''} · {propriete.ville}"


def taches(locations):
    """Toutes les actions en attente. `locations` est le suivi des mises en
    location déjà calculé pour le tableau de bord."""
    aujourd_hui = timezone.localdate()
    # Les mises en location les plus avancées d'abord : ce sont les plus
    # proches d'un loyer.
    liste = [
        Tache("Mises en location", ligne["etape"], _adresse(ligne["propriete"]), ligne["lien"], ligne["action"],
              detail=ligne["detail"], processus=ligne.get("processus"))
        for ligne in sorted(locations, key=lambda ligne: -ligne["processus"].rang if ligne.get("processus") else 0)
    ]

    # Mandats : à faire signer, dossier de pièces à compléter. Les dossiers à
    # compléter d'un même bailleur tiennent sur une ligne : ses pièces à lui
    # (identité, RIB, Kbis) valent pour tous ses mandats.
    mandats = Mandat.objects.filter(date_fin__isnull=True).select_related("propriete__bailleur").prefetch_related(
        "propriete__pieces", "propriete__bailleur__pieces",
    ).order_by("numero")
    a_completer = {}
    for mandat in mandats:
        manquantes = mandat.pieces_manquantes
        if not mandat.signe:
            liste.append(Tache(
                "Mandats", f"Faire signer le mandat n° {mandat.numero}",
                f"{mandat.propriete.bailleur} · {_adresse(mandat.propriete)}", mandat.get_absolute_url(), "Ouvrir le mandat",
                detail=f"{_nb_pieces(len(manquantes))} aussi à fournir" if manquantes else "",
                processus=processus_mandat(mandat, manquantes),
            ))
        elif manquantes:
            a_completer.setdefault(mandat.propriete.bailleur, []).append((mandat, manquantes))
    for bailleur, groupe in sorted(a_completer.items(), key=lambda item: -len(item[1])):
        if len(groupe) == 1:
            mandat, manquantes = groupe[0]
            liste.append(Tache(
                "Mandats", f"Compléter le dossier du mandat n° {mandat.numero}",
                f"{bailleur} · {_adresse(mandat.propriete)}", mandat.get_absolute_url() + "#dossier",
                "Compléter le dossier", detail=f"{_nb_pieces(len(manquantes))} à fournir",
                processus=processus_mandat(mandat, manquantes),
            ))
        else:
            numeros = ", ".join(str(mandat.numero) for mandat, _ in groupe)
            du_bailleur = [
                ligne for ligne in groupe[0][0].dossier
                if ligne["du_bailleur"] and ligne["obligatoire"] and (ligne["piece"] is None or ligne["piece"].perime)
            ]
            liste.append(Tache(
                "Mandats", f"Compléter {len(groupe)} dossiers {de(str(bailleur))}", f"Mandats n° {numeros}",
                bailleur.get_absolute_url(), "Voir le bailleur",
                detail=f"{_nb_pieces(len(du_bailleur))} du bailleur à fournir" if du_bailleur else "",
                processus=processus_mandat(*groupe[0]),
            ))
    sous_mandat = Mandat.objects.filter(date_fin__isnull=True).values("propriete")
    sans_mandat = Propriete.objects.filter(actif=True).exclude(pk__in=sous_mandat).count()
    if sans_mandat:
        liste.append(Tache(
            "Mandats", f"Créer {'les mandats manquants' if sans_mandat > 1 else 'le mandat manquant'}", "Portefeuille",
            f"{reverse('proprietes:liste')}?mandat=sans", "Voir la liste",
            detail=f"{sans_mandat} propriété{'s' if sans_mandat > 1 else ''} sans mandat en cours",
        ))

    # Candidatures : réponse due aux candidats non retenus.
    refus = Candidature.objects.filter(
        statut=Candidature.Statut.NON_RETENUE, refus_envoye_le__isnull=True,
    ).select_related("propriete").prefetch_related("candidats")
    for candidature in refus:
        liste.append(Tache(
            "Candidatures", f"Réponse à envoyer à {candidature.noms or 'un candidat non retenu'}",
            _adresse(candidature.propriete), reverse("candidatures:reponse", args=[candidature.pk]), "Préparer la réponse",
        ))

    # Baux : départ annoncé dont la date est passée.
    departs = Bail.objects.filter(
        date_fin_effective__isnull=True, date_depart_prevue__lt=aujourd_hui,
    ).select_related("propriete").prefetch_related("locataires")
    for bail in departs:
        liste.append(Tache(
            "Baux", f"Départ à confirmer : {bail.noms_locataires or 'locataire'}", _adresse(bail.propriete),
            reverse("contrats:fin", args=[bail.pk]), "Confirmer le départ", urgent=True,
            detail=f"prévu le {bail.date_depart_prevue:%d/%m/%Y}",
        ))

    # Loyers : une ligne par bail suivi qui a des loyers impayés.
    suivis = Bail.objects.filter(suivi_loyers_depuis__isnull=False).select_related("propriete").prefetch_related("locataires")
    for bail in suivis:
        bail.completer_echeancier()
        impayees = [e for e in bail.echeances.filter(paye_le__isnull=True) if e.impayee]
        if impayees:
            nombre, du = len(impayees), sum(e.total for e in impayees)
            liste.append(Tache(
                "Loyers", f"{nombre} loyer{'s' if nombre > 1 else ''} impayé{'s' if nombre > 1 else ''} : {bail.noms_locataires or 'locataire'}",
                _adresse(bail.propriete), f"{bail.get_absolute_url()}#loyers", "Voir les loyers", urgent=True,
                detail=f"{du:.2f} € depuis {impayees[-1].libelle}".replace(".", ","),
            ))

    # Documents : diagnostics périmés ou qui le seront bientôt (le plus récent de chaque type).
    vus = set()
    pieces = Piece.objects.filter(
        propriete__actif=True, type__in=list(Piece.VALIDITE), date_document__isnull=False,
    ).select_related("propriete").order_by("propriete", "type", "-date_document")
    for piece in pieces:
        if (piece.propriete_id, piece.type) in vus:
            continue
        vus.add((piece.propriete_id, piece.type))
        fin = piece.expire_le
        if fin and fin - PREAVIS_VALIDITE <= aujourd_hui:
            perime = fin < aujourd_hui
            liste.append(Tache(
                "Documents", f"{piece.get_type_display()} {'périmé' if perime else 'bientôt périmé'}",
                _adresse(piece.propriete), f"{piece.propriete.get_absolute_url()}?onglet=documents",
                "Déposer le nouveau", urgent=perime, detail=f"{'depuis le' if perime else 'le'} {fin:%d/%m/%Y}",
            ))

    # Fiches : mentions du bail manquantes sur un logement à louer, société sans représentant.
    for propriete in Propriete.objects.filter(actif=True, mise_en_location_le__isnull=False):
        manquants = propriete.champs_manquants_pour_bail()
        if manquants:
            liste.append(Tache(
                "Fiches à compléter", "Fiche à compléter avant le bail", _adresse(propriete),
                reverse("proprietes:propriete_modifier", args=[propriete.pk]), "Compléter la fiche",
                detail=", ".join(str(m) for m in manquants[:3]) + ("…" if len(manquants) > 3 else ""),
            ))
    for bailleur in Bailleur.objects.exclude(type=Bailleur.Type.PERSONNE_PHYSIQUE).filter(representant_nom=""):
        liste.append(Tache(
            "Fiches à compléter", "Représentant de la société à renseigner", bailleur.nom,
            reverse("proprietes:bailleur_modifier", args=[bailleur.pk]), "Compléter la fiche",
            detail="il signe le mandat et le bail",
        ))
    return liste


def par_rubrique(liste):
    """[(rubrique, tâches)] dans l'ordre d'affichage, les urgentes d'abord."""
    groupes = []
    for rubrique in RUBRIQUES:
        lignes = sorted((t for t in liste if t.rubrique == rubrique), key=lambda t: not t.urgent)
        if lignes:
            groupes.append((rubrique, lignes))
    return groupes
