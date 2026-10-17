"""Échéances des baux et des mandats, calculées à partir des dates déjà
saisies : révision annuelle du loyer, fin de période du bail, fin de période
du mandat, attestation d'assurance du locataire.

Rien n'est coché d'avance : une échéance apparaît quand il est temps d'agir et
disparaît quand l'action est enregistrée (révision appliquée ou refusée,
congé reçu, lettre envoyée, attestation reçue) ou quand son délai est passé.
"""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from .models import Bail, IndiceIRL, RevisionLoyer, ajouter_mois, libelle_trimestre

# Une révision est signalée un mois avant sa date ; au-delà d'un an, celle de
# l'année est perdue (article 17-1 de la loi du 6 juillet 1989).
ALERTE_REVISION = dt.timedelta(days=30)
# Le bailleur donne congé au moins 6 mois avant la fin d'un bail nu, 3 mois
# avant celle d'un meublé : on le rappelle 2 mois avant cette limite.
PREAVIS_BAILLEUR_MOIS = {Bail.Type.NU: 6, Bail.Type.MEUBLE: 3}
ALERTE_CONGE_MOIS = 2
# Loi Chatel : lettre au mandant particulier entre 3 mois et 1 mois avant
# l'échéance ; on la rappelle dès 4 mois avant.
ALERTE_MANDAT_MOIS = 4
# Attestation d'assurance : redemandée chaque année, signalée un mois avant.
ALERTE_ASSURANCE = dt.timedelta(days=30)
# Classes énergie dont le loyer ne peut plus augmenter (loi Climat et résilience).
CLASSES_GELEES = {"F", "G"}

_TRIMESTRE = re.compile(r"(?:\b([1-4])\s*(?:er|e|è|ème|eme)?\s*trim\w*|\bT\s*([1-4]))\D*?(\d{4})", re.IGNORECASE)


def trimestre_irl(texte):
    """(trimestre, année) lu dans « 2e trimestre 2026 » ou « T2 2026 », ou None."""
    trouve = _TRIMESTRE.search(texte or "")
    if not trouve:
        return None
    return int(trouve.group(1) or trouve.group(2)), int(trouve.group(3))


def anniversaire(date, ans):
    return ajouter_mois(date, 12 * ans)


def euros(montant):
    """« 1 234,56 € »."""
    return f"{montant:,.2f} €".replace(",", "\u202f").replace(".", ",")


def arrondi(montant):
    return Decimal(montant).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Revision:
    """La révision attendue d'un bail, avec ce qu'il faut pour la calculer."""
    bail: Bail
    date: dt.date  # date de révision prévue au bail
    trimestre: int | None
    annee: int | None  # année de l'IRL nouveau
    ancien_loyer: Decimal
    irl_ancien: Decimal | None
    irl_nouveau: Decimal | None
    gelee: bool  # logement F ou G : pas d'augmentation possible

    @property
    def libelle_irl(self):
        return libelle_trimestre(self.trimestre, self.annee) if self.trimestre else "trimestre à préciser"

    @property
    def nouveau_loyer(self):
        if self.gelee or not (self.irl_ancien and self.irl_nouveau):
            return None
        return arrondi(self.ancien_loyer * self.irl_nouveau / self.irl_ancien)

    @property
    def hausse(self):
        return self.nouveau_loyer - self.ancien_loyer if self.nouveau_loyer is not None else None

    @property
    def en_retard(self):
        return self.date < timezone.localdate()

    @property
    def applicable_le(self):
        """Une révision demandée en retard ne vaut que pour l'avenir."""
        return max(self.date, timezone.localdate())


def revision_due(bail, aujourd_hui=None):
    """La révision à traiter maintenant pour ce bail, ou None. Le bail est
    révisé chaque année à sa date de prise d'effet."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    if not bail.signe or bail.date_fin_effective or not bail.date_effet:
        return None
    traitees = {revision.date_revision for revision in bail.revisions.all()}
    ans = 1
    while (date := anniversaire(bail.date_effet, ans)) - ALERTE_REVISION <= aujourd_hui:
        if date not in traitees and anniversaire(date, 1) > aujourd_hui:
            break
        ans += 1
    else:
        return None
    reference = trimestre_irl(bail.irl_trimestre)
    trimestre = annee = irl_ancien = irl_nouveau = None
    if reference:
        trimestre, annee_reference = reference
        annee = annee_reference + ans
        irl_nouveau = IndiceIRL.valeur_de(trimestre, annee)
        # La première année, l'indice de référence est celui écrit au bail.
        irl_ancien = bail.irl_valeur if ans == 1 else IndiceIRL.valeur_de(trimestre, annee - 1)
    appliquees = [r for r in bail.revisions.all() if r.statut == RevisionLoyer.Statut.APPLIQUEE]
    return Revision(
        bail=bail, date=date, trimestre=trimestre, annee=annee,
        ancien_loyer=appliquees[0].nouveau_loyer if appliquees else bail.loyer,
        irl_ancien=irl_ancien, irl_nouveau=irl_nouveau,
        gelee=bail.propriete.classe_dpe in CLASSES_GELEES,
    )


def prochaine_date_revision(bail, aujourd_hui=None):
    """Prochaine date anniversaire du bail, pour l'afficher quand rien n'est dû."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    ans = 1
    while anniversaire(bail.date_effet, ans) <= aujourd_hui:
        ans += 1
    return anniversaire(bail.date_effet, ans)


def fin_de_periode(bail, aujourd_hui=None):
    """Fin de la période en cours du bail : il se reconduit pour la même
    durée tant qu'aucun congé n'est donné (sauf le bail étudiant)."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    periodes = 1
    while (fin := ajouter_mois(bail.date_effet, bail.duree_mois * periodes) - dt.timedelta(days=1)) < aujourd_hui:
        if bail.type == Bail.Type.ETUDIANT:
            return fin
        periodes += 1
    return fin


def conge_bailleur(bail, aujourd_hui=None):
    """(fin de période, date limite du congé du bailleur) quand il est temps
    de décider de renouveler ou non le bail, sinon None."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    if not bail.signe or bail.date_fin_effective or bail.conge_recu_le:
        return None
    fin = fin_de_periode(bail, aujourd_hui)
    if bail.type == Bail.Type.ETUDIANT:
        limite = fin
    else:
        limite = ajouter_mois(fin + dt.timedelta(days=1), -PREAVIS_BAILLEUR_MOIS[bail.type]) - dt.timedelta(days=1)
    if ajouter_mois(limite, -ALERTE_CONGE_MOIS) <= aujourd_hui <= limite:
        return fin, limite
    return None


def assurance_due(bail, aujourd_hui=None):
    """Date à laquelle redemander l'attestation d'assurance, quand il est
    temps de le faire, sinon None. Sans attestation notée, on la redemande à
    chaque date anniversaire du bail, pendant un mois de part et d'autre."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    if not bail.signe or bail.date_fin_effective or bail.date_effet > aujourd_hui:
        return None
    if bail.attestation_assurance_le:
        echeance = anniversaire(bail.attestation_assurance_le, 1)
        return echeance if echeance - ALERTE_ASSURANCE <= aujourd_hui else None
    ans = max(1, aujourd_hui.year - bail.date_effet.year)
    for date in (anniversaire(bail.date_effet, ans - 1), anniversaire(bail.date_effet, ans), anniversaire(bail.date_effet, ans + 1)):
        if date > bail.date_effet and abs((date - aujourd_hui).days) <= ALERTE_ASSURANCE.days:
            return date
    return None


def protege_par_chatel(mandat):
    return mandat.propriete.bailleur.type != mandat.propriete.bailleur.Type.PERSONNE_MORALE


def periode_mandat(mandat, aujourd_hui=None):
    """(fin de la période en cours, reconduction tacite à cette fin ?) d'un
    mandat signé en cours, ou None s'il est arrivé au bout de ses reconductions."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    if not mandat.date_signature or mandat.date_fin or not mandat.duree_ans:
        return None
    for periode in range(1, mandat.reconductions + 2):
        fin = anniversaire(mandat.date_signature, mandat.duree_ans * periode) - dt.timedelta(days=1)
        if fin >= aujourd_hui:
            return fin, periode <= mandat.reconductions
    return None


def lettre_chatel_envoyee(mandat, fin):
    envoi = mandat.information_reconduction_le
    return bool(envoi) and envoi >= ajouter_mois(fin, -ALERTE_MANDAT_MOIS)


def echeance_mandat(mandat, aujourd_hui=None):
    """(fin de période, reconduction tacite ?) quand une fin de période du
    mandat demande une action, sinon None : la lettre de la loi Chatel au
    mandant protégé, ou un nouveau mandat après la dernière reconduction."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    periode = periode_mandat(mandat, aujourd_hui)
    if not periode:
        return None
    fin, reconduction = periode
    if aujourd_hui < ajouter_mois(fin, -ALERTE_MANDAT_MOIS):
        return None
    # La loi Chatel protège le particulier et le non-professionnel (SCI
    # familiale), pas la société ; passé le dernier jour pour écrire, la
    # ligne ne sert plus.
    if reconduction and (not protege_par_chatel(mandat) or lettre_chatel_envoyee(mandat, fin)
                         or aujourd_hui > ajouter_mois(fin, -1)):
        return None
    return fin, reconduction
