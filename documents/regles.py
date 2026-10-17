"""Registre des règles légales que l'application applique aux baux et aux
mandats : la valeur retenue, le texte qui la fonde et la date de sa dernière
vérification. Une règle se modifie ici, par une PR, quand la veille légale
mensuelle ou une relecture signale un changement."""

from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.parse import quote

from django.utils import timezone

from mandats.models import PLAFOND_BAIL_HORS_ZONE_TENDUE, PLAFOND_BAIL_ZONE_TENDUE, PLAFOND_ETAT_DES_LIEUX

from .generation import BALISES_REQUISES, CLAUSES_PERIMEES, REMPLACEMENTS, defauts_modele, montant

# Au-delà, une règle ou une relecture de modèle est à revérifier.
VALIDITE = timedelta(days=365)

# Date de la dernière relecture des modèles par un avocat, ou None.
RELECTURE_AVOCAT = None

A_JOUR, A_REVERIFIER, A_CORRIGER = "a_jour", "a_reverifier", "a_corriger"
LIBELLES_ETAT = {A_JOUR: "À jour", A_REVERIFIER: "À revérifier", A_CORRIGER: "À corriger"}


def legifrance(texte):
    return "https://www.legifrance.gouv.fr/search/all?query=" + quote(f'"{texte}"')


@dataclass(frozen=True)
class Regle:
    document: str
    libelle: str
    applique: str
    source: str
    texte: str
    verifiee_le: date
    # Modèle dont un défaut (message de ``defauts_modele`` contenant
    # ``signal``) montre que la règle n'y est plus respectée.
    modele: str = ""
    signal: str = ""

    @property
    def url(self):
        return legifrance(self.texte)


VERIFICATION = date(2026, 10, 15)
BAIL, MANDAT = "Bail", "Mandat"
_balises_mandat = BALISES_REQUISES["mandat_gestion"]

REGLES = (
    Regle(BAIL, "Clause résolutoire", "Six semaines après un commandement de payer resté sans effet",
          "Loi du 6 juillet 1989, art. 24, modifié par la loi du 27 juillet 2023", "loi n° 2023-668",
          VERIFICATION, "bail_habitation", "clause résolutoire"),
    Regle(BAIL, "Logement interdit à la location", "Classe G : nouveau bail refusé depuis le 1er janvier 2025",
          "Loi du 6 juillet 1989, art. 6 (loi Climat et résilience)", "loi n° 2021-1104", VERIFICATION),
    Regle(BAIL, "Gel des loyers F et G", "Loyer limité à celui du précédent locataire",
          "Loi du 6 juillet 1989, art. 17-1 (loi Climat et résilience)", "loi n° 2021-1104", VERIFICATION),
    Regle(BAIL, "Dépôt de garantie maximal", "1 mois de loyer hors charges en nu, 2 mois en meublé",
          "Loi du 6 juillet 1989, art. 22 et 25-6", "loi n° 89-462", VERIFICATION),
    Regle(BAIL, "Préavis du locataire", "1 mois en meublé ou en zone tendue, 3 mois sinon",
          "Loi du 6 juillet 1989, art. 15 et 25-8", "loi n° 89-462", VERIFICATION),
    Regle(BAIL, "Honoraires de location du locataire",
          f"{montant(PLAFOND_BAIL_ZONE_TENDUE)} €/m² en zone tendue, "
          f"{montant(PLAFOND_BAIL_HORS_ZONE_TENDUE)} €/m² ailleurs",
          "Décret du 1er août 2014", "décret n° 2014-890", VERIFICATION),
    Regle(BAIL, "Honoraires d'état des lieux du locataire", f"{montant(PLAFOND_ETAT_DES_LIEUX)} €/m²",
          "Décret du 1er août 2014", "décret n° 2014-890", VERIFICATION),
    Regle(MANDAT, "Rétractation du mandant particulier", "14 jours, avec le formulaire en annexe",
          "Code de la consommation, art. L221-18", "L221-18", VERIFICATION,
          "mandat_gestion", _balises_mandat["retractation"]),
    Regle(MANDAT, "Médiateur de la consommation", "Nom et site du médiateur de l'agence dans le mandat",
          "Code de la consommation, art. L616-1", "L616-1", VERIFICATION,
          "mandat_gestion", _balises_mandat["agence.mediateur"]),
    Regle(MANDAT, "Assurance responsabilité civile professionnelle", "Assureur de l'agence cité dans le mandat",
          "Loi Hoguet du 2 janvier 1970, art. 3", "loi n° 70-9", VERIFICATION,
          "mandat_gestion", _balises_mandat["agence.rcp"]),
)


def etat(regle, defauts, aujourdhui=None):
    """(état, explication) d'une règle, d'après les défauts des modèles en
    service et l'âge de sa vérification."""
    aujourdhui = aujourdhui or date.today()
    if regle.modele:
        for defaut in defauts.get(regle.modele, []):
            if regle.signal.lower() in defaut.lower():
                return A_CORRIGER, defaut
    if aujourdhui - regle.verifiee_le > VALIDITE:
        return A_REVERIFIER, "Vérifiée il y a plus d'un an."
    return A_JOUR, ""


def corrigeable(regle, explication):
    """Le défaut signalé est une clause périmée dont la correction est
    validée : la page « Corriger le modèle » peut la proposer."""
    clauses = CLAUSES_PERIMEES.get(regle.modele, {})
    return any(clauses.get(extrait) == explication for extrait in REMPLACEMENTS.get(regle.modele, {}))


def zones_tendues():
    """Ligne du registre pour la liste des communes en zone tendue, que
    l'application télécharge elle-même : à revérifier si le dernier
    téléchargement réussi est trop ancien."""
    from proprietes import zones_tendues as zones
    from proprietes.models import ListeCommunes

    liste = zones.derniere_liste()
    publiee = liste and (liste.publiee_le or liste.le)
    regle = SimpleNamespace(
        libelle="Communes en zone tendue",
        applique=(f"Liste officielle du {timezone.localtime(publiee):%d/%m/%Y}, {liste.en_zone_tendue} communes"
                  if liste else "Liste officielle pas encore téléchargée"),
        source="Décret n° 2013-392, art. 232 du code général des impôts", url=zones.PAGE,
        verifiee_le=liste and timezone.localtime(liste.le).date(),
    )
    if liste and timezone.now() - liste.le <= zones.PEREMPTION:
        return {"regle": regle, "etat": A_JOUR, "libelle_etat": LIBELLES_ETAT[A_JOUR], "explication": ""}
    essai = ListeCommunes.objects.order_by("-le").first()
    explication = (f"Dernier téléchargement en échec : {essai.erreur}" if essai and essai.erreur
                   else "Téléchargement automatique pas encore fait.")
    return {"regle": regle, "etat": A_REVERIFIER, "libelle_etat": LIBELLES_ETAT[A_REVERIFIER],
            "explication": explication}


def registre(aujourdhui=None):
    """Règles groupées par document, chacune avec son état."""
    defauts = {nom: defauts_modele(nom) for nom in {regle.modele for regle in REGLES if regle.modele}}
    groupes = {}
    for regle in REGLES:
        code, explication = etat(regle, defauts, aujourdhui)
        groupes.setdefault(regle.document, []).append({
            "regle": regle, "etat": code, "libelle_etat": LIBELLES_ETAT[code], "explication": explication,
            "corrigeable": code == A_CORRIGER and corrigeable(regle, explication),
        })
    groupes[BAIL].append(zones_tendues())
    return groupes


def resume(groupes):
    lignes = [ligne for groupe in groupes.values() for ligne in groupe]
    return {
        "total": len(lignes),
        "a_corriger": sum(ligne["etat"] == A_CORRIGER for ligne in lignes),
        "a_reverifier": sum(ligne["etat"] == A_REVERIFIER for ligne in lignes),
    }
