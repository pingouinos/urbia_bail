"""Production des documents à partir des modèles Word de l'agence.

Les modèles sont des fichiers .docx ordinaires dans lesquels les parties
variables sont écrites entre doubles accolades ({{ mandant }}), comme le
permet docxtpl. Le PDF est obtenu en confiant le .docx à Gotenberg
(LibreOffice dans un conteneur à part).
"""

import io
import logging
import re
import struct
import time
import uuid
import zipfile
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from docxtpl import DocxTemplate

journal = logging.getLogger(__name__)

MODELES_PAR_DEFAUT = Path(__file__).resolve().parent / "modeles"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ConversionImpossible(Exception):
    """La conversion en PDF a échoué ; le message dit pourquoi, en clair."""


def chemin_modele(nom):
    """Modèle déposé par un administrateur s'il existe, sinon celui livré
    avec l'application."""
    depose = Path(settings.MEDIA_ROOT) / "modeles" / nom
    return depose if depose.exists() else MODELES_PAR_DEFAUT / nom


def montant(valeur):
    """1234.5 → « 1 234,50 » ; 390.00 → « 390 »."""
    if valeur is None:
        return ""
    valeur = Decimal(valeur)
    texte = f"{valeur:,.0f}" if valeur == valeur.to_integral_value() else f"{valeur:,.2f}"
    return texte.replace(",", " ").replace(".", ",")


def generer_docx(nom_modele, contexte):
    document = DocxTemplate(chemin_modele(nom_modele))
    document.render(contexte, autoescape=True)
    flux = io.BytesIO()
    document.save(flux)
    return placer_logo(flux.getvalue())


def dimensions_image(contenu):
    """(type, largeur, hauteur) d'une image PNG ou JPEG, ou None : ce sont les
    deux formats qu'un document Word sait afficher partout."""
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


def placer_logo(contenu_docx):
    """Remplace le logo de l'en-tête par celui que l'agence a déposé sur sa
    fiche. Le logo d'un modèle est l'image dont le texte de remplacement est
    « logo », sinon la première image de l'en-tête. Il garde la place prévue
    par le modèle, sans être déformé."""
    from core.models import Organisation

    logo = Organisation.courante().logo
    if not logo:
        return contenu_docx
    try:
        with logo.open("rb") as fichier:
            image = fichier.read()
    except OSError:
        return contenu_docx
    dimensions = dimensions_image(image)
    if dimensions is None or not all(dimensions[1:]):
        return contenu_docx
    extension, largeur, hauteur = dimensions
    media = f"media/logo-agence.{extension}"
    sortie = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(contenu_docx)) as source, zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as cible:
        parties = {nom: source.read(nom) for nom in source.namelist()}
        place = False
        for nom in [n for n in parties if re.fullmatch(r"word/header\d*\.xml", n)]:
            liens = f"word/_rels/{nom[5:]}.rels"
            if liens not in parties:
                continue
            xml = parties[nom].decode("utf-8")
            images = [d for d in re.finditer(r"<w:drawing>.*?</w:drawing>", xml, re.S) if "r:embed=" in d.group(0)]
            choisie = next((d for d in images if re.search(r'<wp:docPr[^>]*(?:descr|title)="logo"', d.group(0), re.I)),
                           images[0] if images else None)
            if choisie is None:
                continue
            dessin = choisie.group(0)
            identifiant = re.search(r'r:embed="([^"]+)"', dessin).group(1)
            cadre = re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"', dessin)
            if cadre:
                # Le logo tient dans le cadre du modèle en gardant ses proportions.
                cx, cy = int(cadre.group(1)), int(cadre.group(2))
                echelle = min(cx / largeur, cy / hauteur)
                taille = f'cx="{round(largeur * echelle)}" cy="{round(hauteur * echelle)}"'
                dessin = re.sub(r'(<(?:wp:extent|a:ext)) cx="\d+" cy="\d+"', rf"\1 {taille}", dessin)
            parties[nom] = (xml[:choisie.start()] + dessin + xml[choisie.end():]).encode("utf-8")
            relations = parties[liens].decode("utf-8")
            relations = re.sub(
                rf'(<Relationship\b[^>]*\bId="{re.escape(identifiant)}"[^>]*?)\bTarget="[^"]*"', rf'\1Target="{media}"', relations,
            )
            relations = re.sub(
                rf'(<Relationship\b[^>]*?)\bTarget="[^"]*"([^>]*\bId="{re.escape(identifiant)}")', rf'\1Target="{media}"\2', relations,
            )
            parties[liens] = relations.encode("utf-8")
            place = True
        if not place:
            return contenu_docx
        parties[f"word/{media}"] = image
        types = parties["[Content_Types].xml"].decode("utf-8")
        if f'Extension="{extension}"' not in types:
            types = types.replace("<Default ", f'<Default Extension="{extension}" ContentType="image/{extension}"/><Default ', 1)
            parties["[Content_Types].xml"] = types.encode("utf-8")
        for nom, donnees in parties.items():
            cible.writestr(nom, donnees)
    return sortie.getvalue()


def convertir_pdf(contenu_docx, nom="document.docx"):
    frontiere = uuid.uuid4().hex
    corps = (
        f"--{frontiere}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{nom}"\r\n'
        f"Content-Type: {DOCX}\r\n\r\n"
    ).encode() + contenu_docx + f"\r\n--{frontiere}--\r\n".encode()
    requete = urllib.request.Request(
        f"{settings.GOTENBERG_URL}/forms/libreoffice/convert",
        data=corps,
        headers={"Content-Type": f"multipart/form-data; boundary={frontiere}"},
        method="POST",
    )
    # Une seconde tentative si LibreOffice n'était pas encore prêt (erreur 503).
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
