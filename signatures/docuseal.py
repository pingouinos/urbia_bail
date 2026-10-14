"""Client de l'API DocuSeal, hébergé par l'agence (version Pro : la création
d'une demande à partir d'un PDF lui est réservée).

DocuSeal envoie à chaque signataire un e-mail avec son lien, recueille les
signatures dans l'ordre donné, puis scelle le PDF et produit le certificat
de signature. L'application ne garde que les numéros de la demande et des
signataires : les liens vers les fichiers expirent au bout de quarante
minutes et se redemandent quand on en a besoin.
"""

import base64
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

journal = logging.getLogger(__name__)


class DocusealIndisponible(Exception):
    """DocuSeal n'a pas répondu ou a refusé la demande ; le message dit pourquoi, en clair."""


def configure():
    return bool(settings.DOCUSEAL_URL and settings.DOCUSEAL_CLE_API)


def _appeler(methode, chemin, donnees=None):
    if not configure():
        raise DocusealIndisponible("la signature électronique n'est pas configurée (DOCUSEAL_URL et DOCUSEAL_CLE_API)")
    requete = urllib.request.Request(
        f"{settings.DOCUSEAL_URL}/api{chemin}",
        data=json.dumps(donnees).encode() if donnees is not None else None,
        headers={"X-Auth-Token": settings.DOCUSEAL_CLE_API, "Content-Type": "application/json",
                 "Accept": "application/json"},
        method=methode,
    )
    try:
        with urllib.request.urlopen(requete, timeout=60) as reponse:
            corps = reponse.read()
    except urllib.error.HTTPError as erreur:
        detail = erreur.read(300).decode("utf-8", "replace").strip()
        journal.warning("DocuSeal a répondu %s pour %s %s : %s", erreur.code, methode, chemin, detail)
        if erreur.code in (401, 403):
            raise DocusealIndisponible("la clé d'API DocuSeal est refusée, ou la licence Pro manque") from erreur
        raise DocusealIndisponible(f"le service de signature a répondu une erreur {erreur.code}") from erreur
    except (urllib.error.URLError, TimeoutError) as erreur:
        raison = getattr(erreur, "reason", erreur)
        journal.warning("DocuSeal injoignable (%s %s) : %s", methode, chemin, raison)
        raise DocusealIndisponible("le service de signature ne répond pas") from erreur
    return json.loads(corps) if corps else {}


def creer_demande(nom, pdf, signataires, message=None):
    """Envoie le PDF à signer. Chaque signataire est un dict role, name,
    email ; ils signent dans l'ordre de la liste. Les emplacements de
    signature viennent des balises {{…;role=…}} écrites dans le PDF.
    Rend la liste des signataires créés (id, role, submission_id…)."""
    donnees = {
        "name": nom,
        "order": "preserved",
        "send_email": True,
        "documents": [{"name": nom, "file": base64.b64encode(pdf).decode()}],
        "submitters": signataires,
    }
    if message:
        donnees["message"] = message
    reponse = _appeler("POST", "/submissions/pdf", donnees)
    # Selon la version, la réponse est la demande (avec ses signataires) ou
    # directement la liste des signataires.
    if isinstance(reponse, dict):
        soumis = reponse.get("submitters") or []
        for signataire in soumis:
            signataire.setdefault("submission_id", reponse.get("id"))
        return soumis
    return reponse


def demande(identifiant):
    """La demande : statut, signataires (statut, dates) et liens des documents."""
    return _appeler("GET", f"/submissions/{identifiant}")


def relancer(identifiant_signataire):
    """Renvoie au signataire l'e-mail avec son lien."""
    return _appeler("PUT", f"/submitters/{identifiant_signataire}", {"send_email": True})


def archiver(identifiant):
    """Annule la demande : les liens envoyés ne permettent plus de signer."""
    return _appeler("DELETE", f"/submissions/{identifiant}")


def telecharger(url):
    """Contenu d'un document signé, à partir d'un lien fraîchement obtenu."""
    requete = urllib.request.Request(url, headers={"X-Auth-Token": settings.DOCUSEAL_CLE_API})
    try:
        with urllib.request.urlopen(requete, timeout=60) as reponse:
            return reponse.read()
    except (urllib.error.URLError, TimeoutError) as erreur:
        journal.warning("Document signé non téléchargé : %s", getattr(erreur, "reason", erreur))
        raise DocusealIndisponible("le bail signé n'a pas pu être récupéré") from erreur
