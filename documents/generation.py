"""Production des documents à partir des modèles Word de l'agence.

Les modèles sont des fichiers .docx ordinaires dans lesquels les parties
variables sont écrites entre doubles accolades ({{ mandant }}), comme le
permet docxtpl. Le PDF est obtenu en confiant le .docx à Gotenberg
(LibreOffice dans un conteneur à part).
"""

import io
import uuid
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from docxtpl import DocxTemplate

MODELES_PAR_DEFAUT = Path(__file__).resolve().parent / "modeles"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ConversionImpossible(Exception):
    pass


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
    try:
        with urllib.request.urlopen(requete, timeout=60) as reponse:
            return reponse.read()
    except (urllib.error.URLError, TimeoutError) as erreur:
        raise ConversionImpossible(str(erreur)) from erreur
