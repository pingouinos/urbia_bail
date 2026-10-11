"""Production des documents à partir des modèles Word de l'agence.

Les modèles sont des fichiers .docx ordinaires dans lesquels les parties
variables sont écrites entre doubles accolades ({{ mandant }}), comme le
permet docxtpl. Le PDF est obtenu en confiant le .docx à Gotenberg
(LibreOffice dans un conteneur à part).
"""

import io
import logging
import time
import uuid
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
    return flux.getvalue()


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
