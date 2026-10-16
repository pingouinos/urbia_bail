"""Collecte bornée et différences déterministes, sans interprétation juridique."""
import hashlib
import re
import time
from difflib import unified_diff
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.db import transaction
from django.utils import timezone

from .catalogue import HOTES_AUTORISES, SOURCES
from .models import Alerte, Instantane, Source

TAILLE_MAX = 2_000_000
DELAI = 15
DUREE_MAX = 45
REDIRECTIONS_MAX = 3


class ErreurCollecte(Exception):
    pass


def verifier_url(url):
    adresse = urlsplit(url)
    if (adresse.scheme != "https" or adresse.hostname not in HOTES_AUTORISES
            or adresse.port not in (None, 443) or adresse.username or adresse.password):
        raise ErreurCollecte("Adresse hors du catalogue de domaines HTTPS autorisés.")


class SansRedirection(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TextePrincipal(HTMLParser):
    """Ignore menus, pied de page et scripts ; refuse une structure inconnue."""
    BLOCS = {"p", "li", "div", "section", "h1", "h2", "h3", "h4", "tr", "br", "dt", "dd"}
    IGNORES = {"script", "style", "nav", "footer", "noscript", "button", "svg"}
    VIDES = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pile = []
        self.morceaux = []
        self.trouve = False

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.trouve = True
        if tag in self.BLOCS:
            self.morceaux.append("\n")
        if tag not in self.VIDES:
            self.pile.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCS:
            self.morceaux.append("\n")
        if tag in self.pile:
            del self.pile[len(self.pile) - 1 - self.pile[::-1].index(tag):]

    def handle_data(self, data):
        if "main" in self.pile and not self.IGNORES.intersection(self.pile):
            self.morceaux.append(data)

    def texte(self):
        lignes = [re.sub(r"\s+", " ", ligne).strip() for ligne in "".join(self.morceaux).splitlines()]
        texte = "\n".join(ligne for ligne in lignes if ligne)
        if not self.trouve or len(texte) < 200:
            raise ErreurCollecte("Contenu principal absent ou trop court : extraction à vérifier.")
        return texte


def telecharger(url):
    # Respecte le proxy réseau administré de l'hôte, s'il est configuré.
    ouvreur = build_opener(SansRedirection())
    debut = time.monotonic()
    for saut in range(REDIRECTIONS_MAX + 1):
        verifier_url(url)
        try:
            reponse = ouvreur.open(Request(url, headers={
                "User-Agent": "UrbiaGestion-Veille/1.0", "Accept": "text/html",
                "Accept-Encoding": "identity",
            }), timeout=DELAI)
        except HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            destination = exc.headers.get("Location")
            exc.close()
            if not destination or saut == REDIRECTIONS_MAX:
                raise ErreurCollecte("Redirection absente ou trop de redirections.")
            url = urljoin(url, destination)
            continue
        with reponse:
            if reponse.status != 200:
                raise ErreurCollecte(f"Réponse HTTP inattendue : {reponse.status}.")
            if reponse.headers.get_content_type() not in ("text/html", "application/xhtml+xml"):
                raise ErreurCollecte("Type de contenu inattendu (HTML requis).")
            if reponse.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ErreurCollecte("Compression HTTP inattendue.")
            morceaux, taille = [], 0
            while True:
                if time.monotonic() - debut > DUREE_MAX:
                    raise ErreurCollecte("Durée maximale de collecte dépassée.")
                bloc = reponse.read(min(65536, TAILLE_MAX + 1 - taille))
                if not bloc:
                    break
                morceaux.append(bloc)
                taille += len(bloc)
                if taille > TAILLE_MAX:
                    raise ErreurCollecte("Page trop volumineuse.")
            try:
                html = b"".join(morceaux).decode(reponse.headers.get_content_charset() or "utf-8")
            except (UnicodeError, LookupError) as exc:
                raise ErreurCollecte("Encodage de la page illisible.") from exc
        parseur = TextePrincipal()
        parseur.feed(html)
        return url, parseur.texte()
    raise ErreurCollecte("Trop de redirections.")


def collecter(cle):
    reference = SOURCES[cle]
    Source.objects.get_or_create(cle=cle)
    instant = timezone.now()
    try:
        url, texte = telecharger(reference.url)
    except (ErreurCollecte, HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        # L'échec n'efface jamais la dernière preuve de succès ni l'instantané.
        Source.objects.filter(cle=cle).update(dernier_essai=instant, erreur=str(exc)[:500])
        return "erreur"
    empreinte = hashlib.sha256(texte.encode("utf-8")).hexdigest()
    with transaction.atomic():
        source = Source.objects.select_for_update().get(cle=cle)
        precedent = source.instantanes.first()
        resultat = "inchangé"
        if precedent is None or precedent.empreinte != empreinte:
            nouveau = Instantane.objects.create(source=source, url=url, texte=texte, empreinte=empreinte)
            resultat = "référence initiale"
            if precedent:
                differences = "\n".join(unified_diff(
                    precedent.texte.splitlines(), texte.splitlines(),
                    fromfile="Version précédente", tofile="Version collectée", lineterm="", n=2,
                ))
                Alerte.objects.create(avant=precedent, apres=nouveau, differences=differences,
                                      modeles_potentiels=list(reference.modeles))
                resultat = "alerte créée"
        source.dernier_essai = source.dernier_succes = instant
        source.erreur = ""
        source.save(update_fields=["dernier_essai", "dernier_succes", "erreur"])
    return resultat
