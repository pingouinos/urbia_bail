"""Zone tendue déduite de la commune du bien.

La loi du 6 juillet 1989 (préavis d'un mois, encadrement de l'évolution du
loyer) et le décret n° 2014-890 (honoraires de 10 €/m²) s'appliquent dans les
communes des « zones d'urbanisation continue de plus de 50 000 habitants »
listées par le décret n° 2013-392 (article 232 du code général des impôts).
Le ministère du Logement publie cette liste sur data.gouv.fr. L'application la
télécharge d'elle-même une fois par mois, en tâche de fond (voir
``lancer_si_besoin``, ou la commande ``maj_zones_tendues``), et la garde en
base : un bien reconnu reçoit sa zone tendue sans que personne n'ait à y
penser, même si data.gouv.fr est injoignable ce jour-là.

Depuis 2023, le même fichier classe aussi des communes « touristiques et
tendues » : elles paient la taxe sur les logements vacants, mais les règles
du bail n'y changent pas. Elles ne comptent donc pas comme zone tendue ici.
"""

import csv
import io
import json
import logging
import re
import threading
import time
import urllib.request
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

JEU_DE_DONNEES = "liste-des-communes-selon-le-zonage-tlv-1"
API = f"https://www.data.gouv.fr/api/1/datasets/{JEU_DE_DONNEES}/"
PAGE = f"https://www.data.gouv.fr/datasets/{JEU_DE_DONNEES}"

# Une liste téléchargée il y a moins d'un mois n'est pas redemandée.
FRAICHEUR = timedelta(days=30)
# Au-delà, la page « Règles légales » demande de vérifier le téléchargement.
PEREMPTION = timedelta(days=45)
DELAI_RESEAU = 60
# Après un essai, réussi ou non, on attend avant de retenter.
ATTENTE_ESSAI = timedelta(hours=6)
# Chaque processus ne consulte la base qu'une fois par période.
INTERVALLE_CONTROLE = 600

journal = logging.getLogger(__name__)

# Communes en zone tendue sans aucun doute : si l'une manque, le fichier n'a
# pas été compris et l'ancienne liste est gardée.
TEMOINS = {"75056": "Paris", "69123": "Lyon", "31555": "Toulouse"}
# Fourchettes plausibles : quelque 35 000 communes en France, et une zone
# tendue d'environ 1 500 communes. Hors de ces bornes, le fichier a changé de
# forme ou de sens.
MIN_COMMUNES = 30_000
ZONE_TENDUE_MIN, ZONE_TENDUE_MAX = 800, 3_000


class ListeIllisible(Exception):
    """Le fichier téléchargé n'a pas la forme attendue."""


def cle_commune(nom):
    """« Saint-Orens-de-Gameville », « ST ORENS DE GAMEVILLE » et
    « Paris 11e Arrondissement » se comparent sans accents ni tirets, et un
    arrondissement compte pour sa ville."""
    from .models import cle_adresse

    cle = cle_adresse(nom)
    return re.sub(r"\s+\d+\s*(er|e|eme)?\s+arrondissement$", "", cle).strip()


def departement_du_code_postal(code_postal):
    code = (code_postal or "").strip()
    if not re.fullmatch(r"\d{5}", code):
        return ""
    if code.startswith("97"):
        return code[:3]
    if code.startswith("20"):
        return "2A" if code < "20200" else "2B"
    return code[:2]


def departement_du_code_insee(code):
    return code[:3] if code.startswith("97") else code[:2]


def statut(code_postal, ville):
    """(zone tendue, commune) d'après la liste : (True, commune),
    (False, commune), ou (None, None) si la commune est introuvable ou si la
    liste n'a pas encore été téléchargée."""
    from .models import Commune

    departement, cle = departement_du_code_postal(code_postal), cle_commune(ville)
    if not departement or not cle:
        return None, None
    # Les codes postaux corses ne disent pas toujours le département.
    departements = ["2A", "2B"] if departement in ("2A", "2B") else [departement]
    communes = list(Commune.objects.filter(departement__in=departements, cle=cle))
    if not communes or len({c.zone_tendue for c in communes}) > 1:
        return None, None
    return communes[0].zone_tendue, communes[0]


# Lecture du fichier

def _norme(texte):
    return re.sub(r"[^a-z0-9]+", "_", (texte or "").strip().lower()).strip("_")


def _colonne(entetes, tests):
    for test in tests:
        for i, entete in enumerate(entetes):
            if test(_norme(entete)):
                return i
    return None


def en_zone_tendue(valeur):
    """Classement d'une commune lu dans le fichier : seules les zones
    d'urbanisation continue comptent, pas les communes touristiques."""
    from .models import cle_adresse

    # « 1. Zone tendue », « 2. Zone touristique et tendue », « 3. Non tendue ».
    texte = re.sub(r"^\d+\s+", "", cle_adresse(valeur))
    if not texte or "touris" in texte or texte.startswith(("non", "hors", "pas ")):
        return False
    return "tendu" in texte or "urbanisation" in texte


def lire(contenu):
    """Communes du fichier CSV : liste de (code INSEE, nom, zone tendue).
    Le fichier est contrôlé avant d'être retenu."""
    for encodage in ("utf-8-sig", "cp1252"):
        try:
            texte = contenu.decode(encodage)
            break
        except UnicodeDecodeError:
            continue
    premiere = texte.split("\n", 1)[0]
    separateur = max(";,\t|", key=premiere.count)
    lignes = csv.reader(io.StringIO(texte), delimiter=separateur)
    entetes = next(lignes, [])
    code = _colonne(entetes, [
        lambda e: e in ("codgeo", "code_insee", "insee_com", "depcom", "com", "code_commune", "code_commune_insee"),
        lambda e: e.startswith(("codgeo", "code_insee", "insee")),
        lambda e: "code" in e and "com" in e and "post" not in e,
    ])
    nom = _colonne(entetes, [
        lambda e: e.startswith("libgeo") or e in ("nom", "nom_commune", "libelle_commune", "nom_com", "libcom", "lib_com"),
        lambda e: ("nom" in e or "lib" in e) and "dep" not in e and "reg" not in e,
    ])
    # Le fichier garde une colonne par version du zonage (2013, 2023, puis
    # chaque décret modificatif) : la plus à droite est la version en vigueur.
    zones = [i for i, e in enumerate(entetes) if "zonage" in _norme(e) or "tlv" in _norme(e)]
    zone = zones[-1] if zones else None
    if None in (code, nom, zone):
        raise ListeIllisible(f"Colonnes introuvables dans l'en-tête : {', '.join(entetes)}.")
    communes = {}
    for ligne in lignes:
        if len(ligne) <= max(code, nom, zone):
            continue
        insee = ligne[code].strip().upper().zfill(5)
        if re.fullmatch(r"\d[\dAB]\d{3}", insee):
            communes[insee] = (insee, ligne[nom].strip(), en_zone_tendue(ligne[zone]))
    controler(communes)
    return list(communes.values())


def controler(communes):
    tendues = sum(tendue for _, _, tendue in communes.values())
    if len(communes) < MIN_COMMUNES:
        raise ListeIllisible(f"Seulement {len(communes)} communes lues.")
    if not ZONE_TENDUE_MIN <= tendues <= ZONE_TENDUE_MAX:
        raise ListeIllisible(f"{tendues} communes en zone tendue, nombre invraisemblable.")
    manquantes = [nom for code, nom in TEMOINS.items() if not communes.get(code, (0, 0, False))[2]]
    if manquantes:
        raise ListeIllisible(f"{', '.join(manquantes)} absente{'s' if len(manquantes) > 1 else ''} de la zone tendue.")


# Téléchargement

def _ouvrir(url):
    requete = urllib.request.Request(url, headers={"User-Agent": "UrbiaBail (gestion locative)"})
    with urllib.request.urlopen(requete, timeout=DELAI_RESEAU) as reponse:
        return reponse.read()


def telecharger():
    """(contenu du CSV, date de publication) depuis data.gouv.fr."""
    jeu = json.loads(_ouvrir(API))
    fichiers = [r for r in jeu.get("resources", []) if (r.get("format") or "").lower() == "csv"]
    if not fichiers:
        raise ListeIllisible("Aucun fichier CSV dans le jeu de données.")
    fichier = max(fichiers, key=lambda r: r.get("last_modified") or "")
    publiee_le = parse_datetime(fichier.get("last_modified") or "")
    return _ouvrir(fichier["url"]), publiee_le


def derniere_liste():
    from .models import ListeCommunes

    return ListeCommunes.objects.filter(erreur="").order_by("-le").first()


def mettre_a_jour(forcer=False, source=telecharger):
    """Télécharge la liste si elle date de plus d'un mois, puis recalcule la
    zone tendue des biens. Renvoie le ListeCommunes enregistré, ou None si
    la liste était encore fraîche. Une erreur est notée sans toucher à la
    liste en place."""
    from .models import ListeCommunes

    derniere = derniere_liste()
    if not forcer and derniere and timezone.now() - derniere.le < FRAICHEUR:
        return None
    try:
        contenu, publiee_le = source()
        communes = lire(contenu)
    except (OSError, ValueError, KeyError, ListeIllisible) as erreur:
        return ListeCommunes.objects.create(erreur=str(erreur) or erreur.__class__.__name__)
    with transaction.atomic():
        remplacer(communes)
        liste = ListeCommunes.objects.create(
            publiee_le=publiee_le, communes=len(communes),
            en_zone_tendue=sum(tendue for _, _, tendue in communes),
        )
        liste.changements = recalculer_les_biens()
        liste.save(update_fields=["changements"])
    return liste


def remplacer(communes):
    from .models import Commune

    Commune.objects.all().delete()
    Commune.objects.bulk_create(
        [Commune(code_insee=code, nom=nom, departement=departement_du_code_insee(code), cle=cle_commune(nom),
                 zone_tendue=tendue) for code, nom, tendue in communes],
        batch_size=2000,
    )


def recalculer_les_biens():
    """Applique la liste à chaque bien et renvoie ceux dont la zone tendue
    a changé, pour que l'agence vérifie leurs baux en cours."""
    from .models import Propriete

    changements = []
    for propriete in Propriete.objects.all():
        avant = (propriete.zone_tendue, propriete.commune_reconnue)
        propriete.appliquer_zone_tendue()
        if (propriete.zone_tendue, propriete.commune_reconnue) == avant:
            continue
        propriete.save()
        if propriete.zone_tendue != avant[0]:
            changements.append({
                "id": propriete.pk, "bien": str(propriete), "zone_tendue": propriete.zone_tendue,
            })
    return changements


# Mise à jour en tâche de fond, sans tâche planifiée à configurer

_verrou = threading.Lock()
_prochain_controle = 0.0


def a_mettre_a_jour():
    from .models import ListeCommunes

    essai = ListeCommunes.objects.order_by("-le").first()
    if essai and timezone.now() - essai.le < ATTENTE_ESSAI:
        return False
    derniere = derniere_liste()
    return derniere is None or timezone.now() - derniere.le >= FRAICHEUR


def lancer_si_besoin():
    """Appelée à chaque requête (proprietes.middleware) : lance le
    téléchargement dans un fil d'exécution à part quand la liste a plus d'un
    mois, sans faire attendre la page."""
    global _prochain_controle
    if time.monotonic() < _prochain_controle:
        return
    _prochain_controle = time.monotonic() + INTERVALLE_CONTROLE
    if not a_mettre_a_jour() or not _verrou.acquire(blocking=False):
        return
    threading.Thread(target=_mettre_a_jour_en_fond, name="zones-tendues", daemon=True).start()


def _mettre_a_jour_en_fond():
    try:
        liste = mettre_a_jour()
        if liste is not None and liste.erreur:
            journal.warning("Liste des zones tendues non mise à jour : %s", liste.erreur)
    except Exception:
        journal.exception("Mise à jour des zones tendues en échec")
    finally:
        connection.close()
        _verrou.release()
