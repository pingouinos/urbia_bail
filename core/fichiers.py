"""Contrôle et rangement des fichiers déposés.

Un fichier n'est accepté que si son contenu correspond à un format attendu :
l'extension seule ne prouve rien. Il est rangé sous un nom tiré au hasard,
pour qu'un nom choisi par l'utilisateur n'atteigne jamais le disque, et servi
avec un type sûr.
"""

import uuid
import zipfile
from pathlib import PurePath

from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404

# Premiers octets des formats acceptés, et type sous lequel ils sont servis.
FORMATS = {
    "pdf": (b"%PDF-", "application/pdf", (".pdf",)),
    "jpeg": (b"\xff\xd8\xff", "image/jpeg", (".jpg", ".jpeg")),
    "png": (b"\x89PNG\r\n\x1a\n", "image/png", (".png",)),
}
TAILLE_MAX = 20 * 1024 * 1024
# Garde-fous contre une archive piégée (modèle Word, classeur Excel).
ARCHIVE_ENTREES_MAX = 2000
ARCHIVE_TAILLE_MAX = 60 * 1024 * 1024


def format_fichier(debut):
    """Nom du format reconnu d'après les premiers octets, ou None."""
    return next((nom for nom, (signature, _, _) in FORMATS.items() if debut.startswith(signature)), None)


def controler_fichier(fichier, taille_max=TAILLE_MAX):
    """Lève ValidationError si le fichier déposé n'est pas un PDF ou une image
    dont le contenu et l'extension concordent."""
    if fichier.size > taille_max:
        raise ValidationError(f"Fichier trop volumineux ({taille_max // (1024 * 1024)} Mo au maximum).")
    debut = fichier.read(16)
    fichier.seek(0)
    format_ = format_fichier(debut)
    if format_ is None:
        raise ValidationError("Ce fichier n'est ni un PDF ni une image (JPEG, PNG).")
    if not fichier.name.lower().endswith(FORMATS[format_][2]):
        raise ValidationError("Fournir un PDF ou une image : l'extension du fichier ne correspond pas à son contenu.")
    return fichier


def controler_archive(contenu):
    """Lève ValidationError si un fichier Office (une archive zip) est
    illisible ou démesuré une fois décompressé."""
    import io

    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            entrees = archive.infolist()
    except zipfile.BadZipFile:
        raise ValidationError("Ce fichier n'a pas pu être lu.")
    if len(entrees) > ARCHIVE_ENTREES_MAX or sum(entree.file_size for entree in entrees) > ARCHIVE_TAILLE_MAX:
        raise ValidationError("Ce fichier est anormalement volumineux une fois décompressé.")


def nom_aleatoire(nom):
    """Nom de rangement tiré au hasard, qui ne garde du nom d'origine que son extension connue."""
    extension = PurePath(nom).suffix.lower()
    connues = {ext for _, _, extensions in FORMATS.values() for ext in extensions} | {".webp"}
    return f"{uuid.uuid4().hex}{extension if extension in connues else ''}"


def servir(champ, nom=""):
    """Réponse pour un fichier déposé : son type est déduit de son contenu,
    jamais deviné par le navigateur ; tout autre contenu est proposé en
    téléchargement."""
    if not champ:
        raise Http404
    try:
        fichier = champ.open("rb")
    except FileNotFoundError:
        raise Http404
    debut = fichier.read(16)
    fichier.seek(0)
    format_ = format_fichier(debut)
    type_ = FORMATS[format_][1] if format_ else None
    if type_ is None and debut[:4] == b"RIFF" and debut[8:12] == b"WEBP":
        type_ = "image/webp"  # logo de l'agence
    reponse = FileResponse(
        fichier, content_type=type_ or "application/octet-stream", as_attachment=type_ is None, filename=nom or None,
    )
    reponse["X-Content-Type-Options"] = "nosniff"
    reponse["Cache-Control"] = "private, no-store"
    return reponse
