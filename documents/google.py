"""Lecture d'un modèle tenu dans Google Docs.

Le Google Doc est partagé en lecture avec « tous les utilisateurs qui ont le
lien » : l'application le télécharge au format Word par l'adresse d'export,
sans compte Google ni clé d'API. Le modèle ne contient aucune donnée
personnelle, seulement le texte type et les balises.
"""

import re
import urllib.error
import urllib.request

EXPORT = "https://docs.google.com/document/d/{}/export?format=docx"
LIEN = re.compile(r"^https://docs\.google\.com/document/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})")
TAILLE_MAX = 10 * 1024 * 1024


class LectureImpossible(Exception):
    pass


def identifiant(lien):
    """Identifiant du document dans un lien « docs.google.com/document/d/… »,
    ou None."""
    trouve = LIEN.match((lien or "").strip())
    return trouve.group(1) if trouve else None


def telecharger(lien):
    """Contenu .docx du Google Doc."""
    document = identifiant(lien)
    if document is None:
        raise LectureImpossible("Ce lien n'est pas celui d'un Google Doc (docs.google.com/document/d/…).")
    requete = urllib.request.Request(EXPORT.format(document), headers={"User-Agent": "Urbia Gestion"})
    try:
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            contenu = reponse.read(TAILLE_MAX + 1)
    except urllib.error.HTTPError as erreur:
        if erreur.code in (401, 403, 404):
            raise LectureImpossible(
                "Google refuse l'accès : vérifier le lien, et que le document est partagé en lecture avec "
                "« Tous les utilisateurs qui ont le lien »."
            ) from erreur
        raise LectureImpossible(f"Google Docs a répondu par une erreur ({erreur.code}).") from erreur
    except (urllib.error.URLError, TimeoutError) as erreur:
        raise LectureImpossible("Google Docs est injoignable depuis le serveur pour le moment.") from erreur
    if len(contenu) > TAILLE_MAX:
        raise LectureImpossible("Le document dépasse 10 Mo.")
    if not contenu.startswith(b"PK"):
        # Sans partage par lien, Google renvoie sa page de connexion.
        raise LectureImpossible(
            "Le document n'est pas partagé : dans Google Docs, Partager > Accès général > « Tous les "
            "utilisateurs qui ont le lien », rôle Lecteur."
        )
    return contenu
