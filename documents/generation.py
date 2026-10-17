"""Production des documents à partir des modèles HTML de l'agence.

Un modèle est un fragment HTML restreint (voir ``gabarits``) dont les parties
variables sont des données nommées. Le document rempli est une page HTML
complète, mise en page aux couleurs de l'agence (logo, coordonnées, pied de
page), que Gotenberg imprime en PDF A4 avec Chromium. Les anciennes archives
Word restent converties par LibreOffice.
"""

import base64
import copy
import html
import io
import logging
import re
import struct
import time
import uuid
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string

from . import gabarits

journal = logging.getLogger(__name__)

MODELES_PAR_DEFAUT = Path(__file__).resolve().parent / "modeles"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML = "text/html"


class ConversionImpossible(Exception):
    """La conversion en PDF a échoué ; le message dit pourquoi, en clair."""


def fichier(nom):
    return f"{nom}.html"


def convertir_ancien(chemin):
    """Un modèle Word déposé avant le passage au HTML (``chemin`` en .html,
    son ancien fichier en .docx à côté) est converti une fois ; le Word est
    gardé à côté sous un autre nom. Sans conversion possible, il reste."""
    ancien = chemin.with_suffix(".docx")
    if chemin.exists() or not ancien.exists():
        return
    try:
        contenu = gabarits.depuis_docx(ancien.read_bytes())
    except Exception:
        journal.exception("Conversion impossible de l'ancien modèle Word %s", ancien)
        return
    chemin.write_text(contenu, encoding="utf-8")
    ancien.rename(ancien.with_suffix(".docx.avant-html"))


def chemin_modele(nom):
    """Modèle mis en service par un administrateur s'il existe, sinon celui
    livré avec l'application."""
    depose = Path(settings.MEDIA_ROOT) / "modeles" / fichier(nom)
    convertir_ancien(depose)
    return depose if depose.exists() else MODELES_PAR_DEFAUT / fichier(nom)


def lire_modele(nom):
    return chemin_modele(nom).read_text(encoding="utf-8")


# Clauses que la loi a rendues fausses : un modèle qui les contient encore ne
# sert plus à produire un contrat.
CLAUSES_PERIMEES = {
    "bail_habitation": {
        "deux mois après un commandement de payer": (
            "La clause résolutoire du modèle de bail prévoit encore « deux mois après un commandement de "
            "payer » : c'est six semaines depuis la loi du 27 juillet 2023 (article 24 de la loi du "
            "6 juillet 1989). Corriger le modèle dans « Modèles des documents »."
        ),
    },
}


# Correction validée de chaque clause périmée, proposée à l'administrateur
# depuis la page Règles légales (jamais appliquée sans son clic).
REMPLACEMENTS = {
    "bail_habitation": {
        "deux mois après un commandement de payer": "six semaines après un commandement de payer",
    },
}


def corriger_modele(nom, contenu=None):
    """(contenu corrigé, changements) : le modèle en service avec ses clauses
    périmées remplacées par leur correction validée, et les passages du
    modèle d'origine qui portent une mention obligatoire absente remis à leur
    place. Chaque changement est un dict : message, avant, retire, ajoute,
    apres (textes à montrer). Liste vide si rien n'est à corriger."""
    if contenu is None:
        contenu = lire_modele(nom)
    corrige, remplacements = gabarits.remplacer(contenu, REMPLACEMENTS.get(nom, {}))
    changements = []
    for extrait, remplacement, avant, _ in remplacements:
        debut = avant.find(extrait)
        changements.append({
            "message": CLAUSES_PERIMEES[nom][extrait], "avant": avant[:debut], "retire": extrait,
            "ajoute": remplacement, "apres": avant[debut + len(extrait):],
        })
    corrige, ajouts = completer_modele(nom, corrige)
    changements += ajouts
    return (corrige, changements) if changements else (contenu, [])


def _mention_presente(texte, balise):
    return re.search(r"[{%]\s*(if|for \w+ in)?\s*" + re.escape(balise) + r"\b", texte)


def _cle_bloc(noeud):
    """Texte d'un bloc de premier niveau, pour le retrouver d'un modèle à l'autre."""
    return " ".join(gabarits.texte(gabarits.Noeud("div", enfants=[noeud])).split()).lower()


def _lisible(texte):
    """Texte d'un passage pour l'administrateur : données en « [libellé] »,
    sans les lignes de condition ni de répétition."""
    lignes = [ligne for ligne in texte.splitlines() if not re.fullmatch(r"\s*\{%.*%\}\s*", ligne)]
    lignes = ["(texte légal rempli selon le mandat)" if ligne.strip() == "{{ ligne }}" else ligne for ligne in lignes]
    return re.sub(r"\{\{ (.*?) \}\}", lambda m: f"[{gabarits.libelle(m.group(1))}]", "\n".join(lignes))


def completer_modele(nom, contenu):
    """(contenu, ajouts) : chaque mention obligatoire absente reprise du
    modèle d'origine avec son passage (son titre de section s'il manque
    aussi), inséré après le dernier passage d'origine que le modèle de
    l'agence a gardé."""
    balises = BALISES_REQUISES.get(nom, {})
    if not balises:
        return contenu, []
    arbre = gabarits.nettoyer(contenu)
    absentes = [(b, l) for b, l in balises.items() if not _mention_presente(gabarits.texte(arbre), b)]
    if not absentes:
        return contenu, []
    origine = gabarits.nettoyer((MODELES_PAR_DEFAUT / fichier(nom)).read_text(encoding="utf-8")).enfants
    rangs = []
    for balise, libelle in absentes:
        rang = next((i for i, bloc in enumerate(origine)
                     if isinstance(bloc, gabarits.Noeud) and _mention_presente(gabarits.texte(bloc), balise)), None)
        if rang is not None:
            rangs.append((rang, balise, libelle))
    ajouts = []
    # Dans l'ordre du modèle d'origine : chaque passage repris sert de repère au suivant.
    for rang, balise, libelle in sorted(rangs):
        if _mention_presente(gabarits.texte(arbre), balise):
            continue  # reprise avec le passage d'une autre mention
        cles = [_cle_bloc(bloc) for bloc in arbre.enfants]
        debut = rang
        if (rang > 0 and isinstance(origine[rang - 1], gabarits.Noeud) and origine[rang - 1].balise == "h2"
                and _cle_bloc(origine[rang - 1]) not in cles):
            debut = rang - 1
        position = len(arbre.enfants)
        for precedent in reversed(origine[:debut]):
            cle = _cle_bloc(precedent) if isinstance(precedent, gabarits.Noeud) else ""
            if cle and cle in cles:
                position = len(cles) - cles[::-1].index(cle)
                break
        passage = copy.deepcopy(origine[debut:rang + 1])
        arbre.enfants[position:position] = passage
        avant = next((_cle_bloc(bloc) for bloc in reversed(arbre.enfants[:position])
                      if isinstance(bloc, gabarits.Noeud) and _cle_bloc(bloc)), "")
        ajouts.append({
            "message": f"Le modèle ne contient pas {libelle} : le passage du modèle d'origine est repris.",
            "avant": "", "retire": "",
            "ajoute": _lisible(gabarits.texte(gabarits.Noeud("div", enfants=passage))),
            "apres": "", "ancre": _lisible(avant)[:120],
        })
    if not ajouts:
        return contenu, []
    return gabarits.serialiser(arbre), ajouts


def texte_docx(contenu):
    """Texte brut du corps d'un .docx (anciennes archives), paragraphe par paragraphe."""
    with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    paragraphes = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
    return "\n".join("".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", p)) for p in paragraphes)


def texte_document(contenu):
    """Texte lisible d'un document rempli (page HTML), un bloc par ligne."""
    if isinstance(contenu, bytes):
        contenu = contenu.decode("utf-8")
    corps = re.sub(r"<(template|style|head)\b.*?</\1>", "", contenu, flags=re.S)
    corps = re.sub(r"<br\s*/?>", "\n", corps)
    corps = re.sub(r"</(p|h1|h2|h3|li|td|th|div)>", "\n", corps)
    # Les espaces insécables du texte (« Adresse du bien : ») sont gardées.
    lignes = html.unescape(re.sub(r"<[^>]+>", "", corps)).splitlines()
    return "\n".join(re.sub(r"[ \t\r\f\v]+", " ", ligne).strip(" ") for ligne in lignes if ligne.strip())


# Balises sans lesquelles le document omet une mention obligatoire.
BALISES_REQUISES = {
    "mandat_gestion": {
        "retractation": "le droit de rétractation du mandant particulier",
        "formulaire_retractation": "le formulaire de rétractation",
        "agence.mediateur": "le médiateur de la consommation",
        "agence.rcp": "l'assurance responsabilité civile professionnelle",
    },
}


def defauts_modele(nom, contenu=None):
    """Clauses périmées ou mentions obligatoires absentes du modèle (celui en
    service par défaut), en messages à afficher."""
    clauses, balises = CLAUSES_PERIMEES.get(nom, {}), BALISES_REQUISES.get(nom, {})
    if not clauses and not balises:
        return []
    if contenu is None:
        contenu = lire_modele(nom)
    try:
        texte = " ".join(gabarits.texte(gabarits.nettoyer(contenu)).split())
    except gabarits.ModeleInvalide:
        return []
    defauts = [message for extrait, message in clauses.items() if extrait in texte]
    absentes = [libelle for balise, libelle in balises.items()
                if not re.search(r"[{%]\s*(if|for \w+ in)?\s*" + re.escape(balise) + r"\b", texte)]
    if absentes:
        defauts.append(
            "Le modèle ne contient pas " + ", ni ".join(absentes) + " : reprendre ces données du modèle "
            "d'origine dans « Modèles des documents »."
        )
    return defauts


def montant(valeur):
    """1234.5 → « 1 234,50 » ; 390.00 → « 390 »."""
    if valeur is None:
        return ""
    valeur = Decimal(valeur)
    texte = f"{valeur:,.0f}" if valeur == valeur.to_integral_value() else f"{valeur:,.2f}"
    return texte.replace(",", " ").replace(".", ",")


def logo_agence():
    """Logo de la fiche agence en adresse data: (PNG ou JPEG), sinon le logo
    URBIA livré avec l'application pour l'agence URBIA, sinon rien."""
    from core.models import Organisation

    organisation = Organisation.courante()
    image = b""
    if organisation.logo:
        try:
            with organisation.logo.open("rb") as contenu:
                image = contenu.read()
        except OSError:
            image = b""
    if dimensions_image(image) is None:
        image = b""
        if organisation.logo_par_defaut:
            chemin = finders.find(organisation.logo_par_defaut)
            image = Path(chemin).read_bytes() if chemin else b""
    dimensions = dimensions_image(image)
    if dimensions is None:
        return ""
    return f"data:image/{dimensions[0]};base64,{base64.b64encode(image).decode()}"


def generer_html(nom_modele, contexte, source=None):
    """Page HTML complète du document : le modèle (celui en service, ou
    ``source``) rempli, dans la mise en page d'impression. Ses octets sont
    ce qu'archive un contrat préparé."""
    corps = gabarits.remplir(source if source is not None else lire_modele(nom_modele), contexte)
    return render_to_string("documents/impression.html", {
        "corps": corps, "agence": contexte.get("agence") or {}, "logo": logo_agence(),
        "titre": nom_modele.replace("_", " "),
    })


def dimensions_image(contenu):
    """(type, largeur, hauteur) d'une image PNG ou JPEG, ou None : les deux
    formats que tous les lecteurs de PDF affichent."""
    if contenu.startswith(b"\x89PNG\r\n\x1a\n") and len(contenu) >= 24:
        return "png", *struct.unpack(">II", contenu[16:24])
    if contenu.startswith(b"\xff\xd8"):
        position = 2
        while position + 9 < len(contenu) and contenu[position] == 0xFF:
            marqueur, longueur = contenu[position + 1], struct.unpack(">H", contenu[position + 2:position + 4])[0]
            # Segments SOF : ils portent la hauteur puis la largeur.
            if 0xC0 <= marqueur <= 0xCF and marqueur not in (0xC4, 0xC8, 0xCC):
                hauteur, largeur = struct.unpack(">HH", contenu[position + 5:position + 9])
                return "jpeg", largeur, hauteur
            position += 2 + longueur
    return None


PIED = re.compile(rb'<template id="pied-de-page">(.*?)</template>', re.S)


def _formulaire(fichiers, champs=()):
    frontiere = uuid.uuid4().hex
    corps = b""
    for nom, valeur in champs:
        corps += (f"--{frontiere}\r\nContent-Disposition: form-data; name=\"{nom}\"\r\n\r\n{valeur}\r\n").encode()
    for nom, type_, contenu in fichiers:
        corps += (
            f"--{frontiere}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{nom}"\r\n'
            f"Content-Type: {type_}\r\n\r\n"
        ).encode() + contenu + b"\r\n"
    return corps + f"--{frontiere}--\r\n".encode(), frontiere


def convertir_pdf(contenu, nom="document"):
    """PDF d'un document : une page HTML (imprimée par Chromium, avec son
    pied de page sur chaque feuille) ou une ancienne archive Word
    (LibreOffice)."""
    if contenu.startswith(b"PK"):
        corps, frontiere = _formulaire([(f"{Path(nom).stem}.docx", DOCX, contenu)])
        route = "/forms/libreoffice/convert"
    else:
        fichiers = [("index.html", HTML, contenu)]
        pied = PIED.search(contenu)
        if pied:
            fichiers.append(("footer.html", HTML, b"<!doctype html><html><head><meta charset=\"utf-8\"></head><body>"
                             + pied.group(1) + b"</body></html>"))
        corps, frontiere = _formulaire(fichiers, [("preferCssPageSize", "true"), ("printBackground", "true")])
        route = "/forms/chromium/convert/html"
    requete = urllib.request.Request(
        f"{settings.GOTENBERG_URL}{route}",
        data=corps,
        headers={"Content-Type": f"multipart/form-data; boundary={frontiere}"},
        method="POST",
    )
    # Une seconde tentative si le service n'était pas encore prêt (erreur 503).
    for tentative in (1, 2):
        try:
            with urllib.request.urlopen(requete, timeout=60) as reponse:
                return reponse.read()
        except urllib.error.HTTPError as erreur:
            if erreur.code == 503 and tentative == 1:
                time.sleep(2)
                continue
            detail = erreur.read(300).decode("utf-8", "replace").strip()
            journal.warning("Gotenberg a répondu %s pour %s : %s", erreur.code, nom, detail)
            raise ConversionImpossible(f"le service de conversion a répondu une erreur {erreur.code}") from erreur
        except (urllib.error.URLError, TimeoutError) as erreur:
            raison = getattr(erreur, "reason", erreur)
            journal.warning("Gotenberg injoignable à %s : %r", settings.GOTENBERG_URL, raison)
            if isinstance(raison, TimeoutError):
                raise ConversionImpossible("la conversion a dépassé le délai d'une minute") from erreur
            raise ConversionImpossible(
                "le service de conversion ne répond pas : il est arrêté ou en cours de redémarrage"
            ) from erreur
