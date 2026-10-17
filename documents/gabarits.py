"""Modèles des documents en HTML.

Un modèle est un fragment HTML restreint, celui que produit l'éditeur de la
page « Modifier le texte » : titres, paragraphes, gras, italique, souligné,
listes, tableaux, sauts de page. Les parties variables n'y sont jamais du
texte libre :

- ``<span data-donnee="bailleur"></span>`` : une donnée du document ;
- ``<span data-case="assurance_loyers_impayes"></span>`` : une case cochée
  ou non (☒ / ☐) ;
- ``<div data-si="colocation">…</div>`` : un passage imprimé seulement si
  la donnée est renseignée ;
- ``<div data-pour="locataire in locataires">…</div>`` : un passage répété
  pour chaque élément d'une liste.

Tout HTML reçu (éditeur, ancien modèle Word converti) passe par ``nettoyer``,
qui ne garde que ces éléments et refuse une expression qui ne serait pas un
simple chemin vers une donnée. Le remplissage se fait ensuite dans un
environnement Jinja protégé, où le texte du modèle ne peut pas devenir une
instruction.
"""

import html
import io
import re
from html.parser import HTMLParser

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup, escape

CHEMIN = r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*"
EXPRESSIONS = {
    "data-donnee": re.compile(rf"{CHEMIN}(?:\|upper)?"),
    "data-case": re.compile(rf"(?:not )?{CHEMIN}"),
    "data-si": re.compile(rf"(?:not )?{CHEMIN}"),
    "data-pour": re.compile(rf"[a-z][a-z0-9_]* in {CHEMIN}"),
}
BLOCS = {"h2", "h3", "p", "ul", "ol", "li", "hr", "table", "tbody", "tr", "td", "th", "div"}
EN_LIGNE = {"strong", "em", "u", "br", "span"}
VIDES = {"br", "hr"}
SYNONYMES = {"b": "strong", "i": "em", "thead": "tbody", "h1": "h2", "h4": "h3", "h5": "h3", "h6": "h3"}
# Éléments dont le contenu ne doit jamais ressortir, même en texte.
IGNORES = {"script", "style", "template", "iframe", "object", "noscript", "colgroup", "head", "title"}
ALIGNEMENTS = {"left", "center", "right", "justify"}
TYPES_LISTE = {"1", "a", "A", "i", "I"}


class ModeleInvalide(ValueError):
    """Le HTML reçu ne peut pas servir de modèle ; le message dit pourquoi."""


class Noeud:
    __slots__ = ("balise", "attributs", "enfants")

    def __init__(self, balise, attributs=None, enfants=None):
        self.balise, self.attributs, self.enfants = balise, dict(attributs or {}), list(enfants or [])

    def __repr__(self):
        return f"<{self.balise} {self.attributs} {self.enfants!r}>"


def _entier(valeur, minimum, maximum):
    try:
        nombre = int(valeur)
    except (TypeError, ValueError):
        return None
    return str(nombre) if minimum <= nombre <= maximum else None


def _attributs(balise, attributs):
    """Attributs conservés pour un élément, expressions vérifiées."""
    garde = {}
    for nom, valeur in attributs:
        valeur = (valeur or "").strip()
        if nom in EXPRESSIONS:
            if (nom in ("data-donnee", "data-case") and balise == "span") or (nom in ("data-si", "data-pour") and balise == "div"):
                expression = " ".join(valeur.split())
                if not EXPRESSIONS[nom].fullmatch(expression):
                    raise ModeleInvalide(f"Une donnée du modèle est mal écrite : « {expression[:60]} ».")
                garde[nom] = expression
        elif nom == "style" and balise in ("p", "h2", "h3", "td", "th"):
            alignement = re.search(r"text-align\s*:\s*([a-z]+)", valeur)
            # Le texte est justifié par défaut : seuls les autres alignements s'écrivent.
            if alignement and alignement.group(1) in ALIGNEMENTS and alignement.group(1) != "justify":
                garde["style"] = f"text-align: {alignement.group(1)}"
        elif nom == "start" and balise == "ol":
            if _entier(valeur, 2, 999):
                garde["start"] = _entier(valeur, 2, 999)
        elif nom == "type" and balise == "ol":
            if valeur in TYPES_LISTE and valeur != "1":
                garde["type"] = valeur
        elif nom in ("colspan", "rowspan") and balise in ("td", "th"):
            if _entier(valeur, 2, 20):
                garde[nom] = _entier(valeur, 2, 20)
        elif nom == "data-bordure" and balise == "table" and valeur == "non":
            garde[nom] = "non"
    return garde


class _Lecteur(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.racine = Noeud("racine")
        self.pile = [self.racine]
        self.ignore = 0

    def handle_starttag(self, balise, attributs):
        balise = SYNONYMES.get(balise, balise)
        if balise in IGNORES:
            self.ignore += 1
            return
        if self.ignore:
            return
        if balise not in BLOCS | EN_LIGNE:
            return
        garde = _attributs(balise, attributs)
        if balise == "span" and not ({"data-donnee", "data-case"} & garde.keys()):
            return
        if balise == "div" and not ({"data-si", "data-pour"} & garde.keys()):
            return
        noeud = Noeud(balise, garde)
        self.pile[-1].enfants.append(noeud)
        if balise not in VIDES:
            self.pile.append(noeud)

    def handle_startendtag(self, balise, attributs):
        self.handle_starttag(balise, attributs)
        balise = SYNONYMES.get(balise, balise)
        if balise not in VIDES and len(self.pile) > 1 and self.pile[-1].balise == balise and not self.pile[-1].enfants:
            self.pile.pop()

    def handle_endtag(self, balise):
        balise = SYNONYMES.get(balise, balise)
        if balise in IGNORES:
            self.ignore = max(0, self.ignore - 1)
            return
        if self.ignore or balise in VIDES:
            return
        for position in range(len(self.pile) - 1, 0, -1):
            if self.pile[position].balise == balise:
                del self.pile[position:]
                return
        # Balise fermante d'un élément retiré (span, div ordinaires) : rien à faire.

    def handle_data(self, texte):
        if self.ignore or not texte:
            return
        parent = self.pile[-1]
        if parent.balise == "span":
            return  # le libellé d'une donnée n'est pas du texte du modèle
        parent.enfants.append(texte)


def _mise_en_forme(noeud):
    """Le texte d'un bloc vit dans un paragraphe : retire les blancs de
    mise en page entre blocs, enveloppe le texte nu des cellules et des
    passages conditionnels."""
    enfants = []
    contient_blocs = noeud.balise in ("racine", "div", "ul", "ol", "table", "tbody", "tr", "td", "th", "li")
    for enfant in noeud.enfants:
        if isinstance(enfant, Noeud):
            _mise_en_forme(enfant)
        enfants.append(enfant)
    if contient_blocs:
        regroupes, courant = [], None
        for enfant in enfants:
            en_ligne = isinstance(enfant, str) or enfant.balise in EN_LIGNE
            if en_ligne:
                if isinstance(enfant, str) and not enfant.strip() and courant is None:
                    continue
                if courant is None:
                    courant = Noeud("p")
                    regroupes.append(courant)
                courant.enfants.append(enfant)
            else:
                courant = None
                regroupes.append(enfant)
        enfants = regroupes
    if noeud.balise in ("ul", "ol"):
        enfants = [e if e.balise == "li" else Noeud("li", enfants=[e]) for e in enfants]
    if noeud.balise == "table":
        lignes = [e for e in enfants if e.balise == "tr"] + [l for e in enfants if e.balise == "tbody" for l in e.enfants
                                                             if isinstance(l, Noeud) and l.balise == "tr"]
        enfants = [Noeud("tbody", enfants=lignes)] if lignes else []
    if noeud.balise == "tr":
        enfants = [e for e in enfants if e.balise in ("td", "th")]
    if noeud.balise == "tbody":
        enfants = [e for e in enfants if e.balise == "tr"]
    noeud.enfants = enfants


def nettoyer(source):
    """Arbre du modèle à partir de n'importe quel HTML : seuls les éléments
    permis restent, une expression mal écrite est refusée."""
    lecteur = _Lecteur()
    lecteur.feed(source or "")
    lecteur.close()
    _mise_en_forme(lecteur.racine)
    return lecteur.racine


def _ouvrir(noeud):
    attributs = "".join(f' {nom}="{html.escape(valeur)}"' for nom, valeur in sorted(noeud.attributs.items()))
    return f"<{noeud.balise}{attributs}>"


def _texte(texte, jinja):
    texte = html.escape(texte, quote=False)
    return texte.replace("{", "&#123;").replace("}", "&#125;") if jinja else texte


def serialiser(arbre, mode="modele", alertes=()):
    """HTML de l'arbre. ``modele`` : la forme enregistrée ; ``editeur`` : les
    données portent leur libellé lisible, et un paragraphe qui contient un
    extrait d'``alertes`` (extrait, message) porte le message ; ``jinja`` : le
    gabarit à remplir."""
    morceaux = []

    def ecrire(noeud):
        if isinstance(noeud, str):
            morceaux.append(_texte(noeud, mode == "jinja"))
            return
        attributs = noeud.attributs
        if mode == "jinja":
            if "data-donnee" in attributs:
                morceaux.append("{{ " + attributs["data-donnee"] + " }}")
                return
            if "data-case" in attributs:
                morceaux.append("{% if " + attributs["data-case"] + " %}☒{% else %}☐{% endif %}")
                return
            if "data-si" in attributs or "data-pour" in attributs:
                mot, fin = ("if", "endif") if "data-si" in attributs else ("for", "endfor")
                morceaux.append("{% " + mot + " " + (attributs.get("data-si") or attributs["data-pour"]) + " %}\n")
                for enfant in noeud.enfants:
                    ecrire(enfant)
                morceaux.append("{% " + fin + " %}\n")
                return
        if mode == "editeur" and alertes and noeud.balise in ("p", "h2", "h3"):
            contenu = " ".join(texte(noeud).split())
            message = next((m for extrait, m in alertes if extrait in contenu), None)
            if message:
                noeud = Noeud(noeud.balise, {**attributs, "data-alerte": message}, noeud.enfants)
        morceaux.append(_ouvrir(noeud))
        if noeud.balise in ("div", "ul", "ol", "table", "tbody", "tr"):
            morceaux.append("\n")
        if noeud.balise == "span":
            if mode == "editeur":
                code = attributs.get("data-donnee") or attributs.get("data-case")
                morceaux.append(html.escape(libelle(code, "data-case" in attributs)))
        elif noeud.balise not in VIDES:
            for enfant in noeud.enfants:
                ecrire(enfant)
        if noeud.balise not in VIDES:
            morceaux.append(f"</{noeud.balise}>")
        if noeud.balise in BLOCS:
            morceaux.append("\n")

    for enfant in arbre.enfants:
        ecrire(enfant)
    return "".join(morceaux)


def pour_editeur(arbre, alertes=()):
    return serialiser(arbre, "editeur", alertes)


def titres(arbre):
    """Intitulés des sections (h2), pour le sommaire de l'éditeur."""
    return [" ".join(texte(Noeud("racine", enfants=[n])).split()) for n in parcourir(arbre)
            if isinstance(n, Noeud) and n.balise == "h2"]


def normaliser(source):
    """Forme enregistrée d'un modèle reçu (nettoyée, une ligne par bloc)."""
    return serialiser(nettoyer(source))


def _humain(expression):
    expression = expression.split("|")[0].strip().removeprefix("not ")
    return expression.replace("_", " ").replace(".", " · ")


def libelle(code, case=False):
    """Libellé lisible d'une donnée : « bailleur email »."""
    return ("Case : " if case else "") + _humain(code or "")


def libelle_condition(attributs):
    if "data-si" in attributs:
        condition = attributs["data-si"]
        return ("Seulement si non : " if condition.startswith("not ") else "Seulement si : ") + _humain(condition)
    element, _, liste = attributs.get("data-pour", "").partition(" in ")
    return f"Pour chaque {_humain(element)} ({_humain(liste)})"


def parcourir(noeud):
    yield noeud
    if isinstance(noeud, Noeud):
        for enfant in noeud.enfants:
            yield from parcourir(enfant)


def expressions(arbre, attribut):
    """Expressions d'un genre (data-donnee, data-si…), dans l'ordre, sans doublon."""
    vues = []
    for noeud in parcourir(arbre):
        if isinstance(noeud, Noeud) and attribut in noeud.attributs and noeud.attributs[attribut] not in vues:
            vues.append(noeud.attributs[attribut])
    return vues


def texte(arbre):
    """Texte brut du modèle, un bloc par ligne, balises écrites comme dans
    un gabarit ({{ … }}, {% if … %}) : pour rechercher une clause ou une
    mention obligatoire."""
    lignes, courante = [], []

    def ecrire(noeud):
        if isinstance(noeud, str):
            courante.append(noeud)
            return
        attributs = noeud.attributs
        if "data-donnee" in attributs:
            courante.append("{{ " + attributs["data-donnee"] + " }}")
        elif "data-case" in attributs:
            courante.append("{% if " + attributs["data-case"] + " %}")
        elif "data-si" in attributs or "data-pour" in attributs:
            fin_ligne()
            lignes.append("{% if " + attributs["data-si"] + " %}" if "data-si" in attributs
                          else "{% for " + attributs["data-pour"] + " %}")
            for enfant in noeud.enfants:
                ecrire(enfant)
            fin_ligne()
            lignes.append("{% endif %}" if "data-si" in attributs else "{% endfor %}")
        elif noeud.balise == "br":
            courante.append("\n")
        else:
            for enfant in noeud.enfants:
                ecrire(enfant)
            if noeud.balise in BLOCS:
                fin_ligne()

    def fin_ligne():
        if courante:
            lignes.append("".join(courante))
            courante.clear()

    for enfant in arbre.enfants:
        ecrire(enfant)
    fin_ligne()
    return "\n".join(lignes)


def _environnement():
    def finaliser(valeur):
        # Une valeur sur plusieurs lignes (conditions particulières) garde ses retours à la ligne.
        if valeur is None:
            return ""
        return Markup(str(escape(valeur)).replace("\n", "<br>\n"))
    return SandboxedEnvironment(autoescape=True, finalize=finaliser)


def gabarit(source):
    return serialiser(nettoyer(source), "jinja")


def variables(source):
    """Données de premier niveau qu'utilise le modèle (agence, bailleur…)."""
    environnement = _environnement()
    return meta.find_undeclared_variables(environnement.parse(gabarit(source)))


def remplir(source, contexte):
    """Corps HTML du document, rempli avec les données du contexte."""
    return _environnement().from_string(gabarit(source)).render(contexte)


def remplacer(source, remplacements):
    """(modèle, [(extrait, remplacement, avant, après)]) : chaque extrait
    remplacé dans le premier bloc qui le contient, même réparti entre du gras
    et du texte ordinaire ; le remplacement prend la mise en forme du morceau
    où commence l'extrait."""
    arbre = nettoyer(source)
    changements = []

    def textes(noeud):
        for position, enfant in enumerate(noeud.enfants):
            if isinstance(enfant, str):
                yield noeud, position
            elif enfant.balise in EN_LIGNE and enfant.balise != "span":
                yield from textes(enfant)

    for bloc in parcourir(arbre):
        if not isinstance(bloc, Noeud) or bloc.balise not in ("p", "h2", "h3", "li"):
            continue
        for extrait, remplacement in remplacements.items():
            morceaux = list(textes(bloc))
            avant = "".join(parent.enfants[i] for parent, i in morceaux)
            debut = avant.find(extrait)
            if debut < 0:
                continue
            fin, position = debut + len(extrait), 0
            for parent, i in morceaux:
                morceau = parent.enfants[i]
                a, b = max(debut - position, 0), min(fin - position, len(morceau))
                if a < b:
                    parent.enfants[i] = morceau[:a] + (remplacement if position <= debut else "") + morceau[b:]
                position += len(morceau)
            apres = "".join(parent.enfants[i] for parent, i in morceaux)
            changements.append((extrait, remplacement, avant, apres))
    return serialiser(arbre), changements


# --- Conversion des anciens modèles Word ------------------------------------

BALISE_WORD = re.compile(r"\{\{\s*(.*?)\s*\}\}|\{%\s*if\s+(.*?)\s*%\}☒\{%\s*else\s*%\}☐\{%\s*endif\s*%\}")
CONTROLE_WORD = re.compile(r"^\s*\{%p?\s+(if|for|endif|endfor)\b\s*(.*?)\s*%\}\s*$")


def depuis_docx(contenu):
    """Modèle HTML équivalent à un modèle Word (docxtpl) : même texte, mêmes
    données, mêmes passages conditionnels ; gras, italique, souligné,
    alignement, listes, tableaux et sauts de page repris. Les images du
    corps ne sont pas reprises : le logo vient de la fiche agence."""
    from docx import Document
    from docx.oxml.ns import qn

    document = Document(io.BytesIO(contenu))
    formats = _formats_listes(document, qn)
    racine = Noeud("racine")
    pile = [racine]
    compteurs, liste, signatures, vides = {}, None, None, 0

    def vider():
        # Les lignes vides servaient à aérer ou à pousser la suite à la page suivante.
        nonlocal vides
        if vides:
            pile[-1].enfants.append(Noeud("hr") if vides >= 8 else Noeud("p"))
            vides = 0

    def deposer(noeud):
        vider()
        pile[-1].enfants.append(noeud)

    for element in document.element.body:
        if element.tag not in (qn("w:p"), qn("w:tbl")):
            continue
        if element.tag == qn("w:tbl"):
            liste = signatures = None
            deposer(_tableau_docx(element, qn))
            continue
        ppr = element.find(qn("w:pPr"))
        controle = CONTROLE_WORD.match("".join(_texte_run(run, qn) for run in element.iter(qn("w:r"))))
        if controle:
            liste = signatures = None
            vider()
            mot, reste = controle.groups()
            if mot in ("if", "for"):
                div = Noeud("div", {"data-si" if mot == "if" else "data-pour": " ".join(reste.split())})
                deposer(div)
                pile.append(div)
            elif len(pile) > 1:
                pile.pop()
            continue
        if ppr is not None and ppr.find(qn("w:pageBreakBefore")) is not None:
            liste = signatures = None
            deposer(Noeud("hr"))
        segments = _segments(element, qn)
        vide = not _texte_segments(segments).strip()
        if vide:
            liste = signatures = None
            if any(br.get(qn("w:type")) == "page" for br in element.iter(qn("w:br"))):
                vides = 0
                deposer(Noeud("hr"))
            else:
                vides += 1
            continue
        alignement, numero, colonnes = _alignement(ppr, qn), _numero(ppr, qn), _colonnes(segments)
        if colonnes and not numero:
            liste = None
            if signatures is None:
                signatures = Noeud("table", {"data-bordure": "non"}, [Noeud("tbody")])
                deposer(signatures)
            signatures.enfants[0].enfants.append(
                Noeud("tr", enfants=[Noeud("td", enfants=[Noeud("p", enfants=_en_ligne(c))]) for c in colonnes]))
            continue
        signatures = None
        segments = _sans_tabulations(segments)
        if numero:
            genre, depart, type_ = formats.get(numero, ("ul", 1, None))
            if liste is None or liste[0] != numero:
                noeud = Noeud(genre)
                if genre == "ol":
                    if compteurs.get(numero, depart) > 1:
                        noeud.attributs["start"] = str(compteurs.get(numero, depart))
                    if type_:
                        noeud.attributs["type"] = type_
                liste = (numero, noeud)
                deposer(noeud)
            if genre == "ol":
                compteurs[numero] = compteurs.get(numero, depart) + 1
            liste[1].enfants.append(Noeud("li", enfants=[Noeud("p", _style(alignement), _en_ligne(segments))]))
            continue
        liste = None
        titre = _titre(segments, alignement)
        if titre:
            deposer(Noeud(titre, _style(alignement), _en_ligne(_sans_marques(segments, {"b", "u"}))))
        else:
            deposer(Noeud("p", _style(alignement), _en_ligne(segments)))
    return serialiser(racine)


def _texte_run(run, qn):
    morceaux = []
    for enfant in run:
        if enfant.tag == qn("w:t"):
            morceaux.append(enfant.text or "")
        elif enfant.tag == qn("w:tab"):
            morceaux.append("\t")
        elif enfant.tag in (qn("w:br"), qn("w:cr")) and enfant.get(qn("w:type")) != "page":
            morceaux.append("\n")
    return "".join(morceaux)


def _actif(rpr, balise, qn):
    if rpr is None:
        return False
    element = rpr.find(qn(balise))
    return element is not None and element.get(qn("w:val")) not in ("0", "false", "none")


def _segments(paragraphe, qn):
    """[(genre, valeur, marques)] du paragraphe : du texte, une donnée ou une
    case, même quand Word a coupé la balise entre plusieurs morceaux."""
    caracteres = []
    for run in paragraphe.iter(qn("w:r")):
        rpr = run.find(qn("w:rPr"))
        marques = frozenset(m for m, b in (("b", "w:b"), ("i", "w:i"), ("u", "w:u")) if _actif(rpr, b, qn))
        # Les puces Wingdings de Word (caractères privés) deviennent des cases à cocher.
        caracteres.extend((c, marques) for c in re.sub("[\uf000-\uf0ff]", "☐", _texte_run(run, qn)))
    texte_complet = "".join(c for c, _ in caracteres)
    segments, position = [], 0

    def ajouter_texte(debut, fin):
        for c, marques in caracteres[debut:fin]:
            if segments and segments[-1][0] == "texte" and segments[-1][2] == marques:
                segments[-1] = ("texte", segments[-1][1] + c, marques)
            else:
                segments.append(("texte", c, marques))

    for balise in BALISE_WORD.finditer(texte_complet):
        ajouter_texte(position, balise.start())
        marques = caracteres[balise.start()][1]
        if balise.group(1) is not None:
            segments.append(("donnee", " ".join(balise.group(1).split()).replace(" | ", "|").replace("| ", "|"), marques))
        else:
            segments.append(("case", " ".join(balise.group(2).split()), marques))
        position = balise.end()
    ajouter_texte(position, len(texte_complet))
    if any("{%" in s[1] or "{{" in s[1] for s in segments if s[0] == "texte"):
        raise ModeleInvalide("Le modèle Word contient une balise que l'éditeur ne sait pas reprendre : "
                             + next(s[1] for s in segments if s[0] == "texte" and "{" in s[1]).strip()[:80])
    return segments


def _texte_segments(segments):
    return "".join(s[1] if s[0] == "texte" else "x" for s in segments)


def _colonnes(segments):
    """Deux textes séparés par plusieurs tabulations (« Le mandant ⇥⇥⇥ Le
    mandataire ») : les deux colonnes de la ligne, sinon None."""
    texte_complet = _texte_segments(segments)
    separation = re.search(r"\S(\t{2,})\S", texte_complet)
    if not separation:
        return None
    debut, fin = separation.start(1), separation.end(1)
    gauche, droite, position = [], [], 0
    for genre, valeur, marques in segments:
        longueur = len(valeur) if genre == "texte" else 1
        for cible, a, b in ((gauche, 0, debut), (droite, fin, len(texte_complet))):
            x, y = max(a - position, 0), min(b - position, longueur)
            if x < y:
                cible.append((genre, valeur[x:y] if genre == "texte" else valeur, marques))
        position += longueur
    return [_sans_tabulations(gauche), _sans_tabulations(droite)]


def _sans_tabulations(segments):
    """Une tabulation de Word n'a pas d'équivalent dans le texte : elle
    devient une espace, et disparaît en début ou en fin de ligne."""
    resultat = [(g, re.sub(r"\t+", " ", v) if g == "texte" else v, m) for g, v, m in segments]
    if resultat and resultat[0][0] == "texte":
        resultat[0] = ("texte", resultat[0][1].lstrip(" "), resultat[0][2])
    if resultat and resultat[-1][0] == "texte":
        resultat[-1] = ("texte", resultat[-1][1].rstrip(" "), resultat[-1][2])
    return [s for s in resultat if s[0] != "texte" or s[1]]


def _sans_marques(segments, retirer):
    return [(genre, valeur, marques - retirer) for genre, valeur, marques in segments]


def _en_ligne(segments):
    """Contenu HTML d'un paragraphe : <strong>, <em>, <u> imbriqués dans cet
    ordre, sauts de ligne en <br>."""
    enfants = []
    for genre, valeur, marques in segments:
        if genre == "texte":
            contenu = []
            for i, ligne in enumerate(valeur.split("\n")):
                if i:
                    contenu.append(Noeud("br"))
                if ligne:
                    contenu.append(ligne)
        else:
            contenu = [Noeud("span", {"data-donnee" if genre == "donnee" else "data-case": valeur})]
        for marque, balise in (("u", "u"), ("i", "em"), ("b", "strong")):
            if marque in marques and contenu:
                contenu = [Noeud(balise, enfants=contenu)]
        for noeud in contenu:
            precedent = enfants[-1] if enfants else None
            if (isinstance(noeud, Noeud) and isinstance(precedent, Noeud) and noeud.balise in ("strong", "em", "u")
                    and precedent.balise == noeud.balise):
                precedent.enfants.extend(noeud.enfants)
            else:
                enfants.append(noeud)
    return enfants


def _alignement(ppr, qn):
    if ppr is None or ppr.find(qn("w:jc")) is None:
        return None
    return {"center": "center", "right": "right", "end": "right"}.get(
        ppr.find(qn("w:jc")).get(qn("w:val")))


def _style(alignement):
    return {"style": f"text-align: {alignement}"} if alignement else {}


def _numero(ppr, qn):
    numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
    if numpr is None or numpr.find(qn("w:numId")) is None:
        return None
    num_id = numpr.find(qn("w:numId")).get(qn("w:val"))
    return None if num_id == "0" else num_id


def _formats_listes(document, qn):
    """{numId: ("ol" | "ul", départ, type)} d'après la numérotation du Word."""
    try:
        numerotation = document.part.numbering_part.element
    except (AttributeError, NotImplementedError, KeyError):
        return {}
    abstraits = {}
    for abstrait in numerotation.findall(qn("w:abstractNum")):
        niveau = next((n for n in abstrait.findall(qn("w:lvl")) if n.get(qn("w:ilvl")) == "0"), None)
        if niveau is None:
            continue
        format_ = niveau.find(qn("w:numFmt"))
        format_ = format_.get(qn("w:val")) if format_ is not None else "bullet"
        depart = niveau.find(qn("w:start"))
        depart = int(depart.get(qn("w:val"))) if depart is not None else 1
        type_ = {"upperRoman": "I", "lowerRoman": "i", "upperLetter": "A", "lowerLetter": "a"}.get(format_)
        abstraits[abstrait.get(qn("w:abstractNumId"))] = ("ul", 1, None) if format_ in ("bullet", "none") else ("ol", depart, type_)
    formats = {}
    for num in numerotation.findall(qn("w:num")):
        abstrait = num.find(qn("w:abstractNumId"))
        if abstrait is not None and abstrait.get(qn("w:val")) in abstraits:
            formats[num.get(qn("w:numId"))] = abstraits[abstrait.get(qn("w:val"))]
    return formats


def _titre(segments, alignement):
    """h2 pour un intitulé de section en gras, h3 s'il est aussi souligné ;
    une ligne centrée reste un paragraphe (titre du contrat)."""
    textes = [s for s in segments if s[1].strip()]
    if not textes or alignement == "center" or any(s[0] != "texte" for s in segments):
        return None
    contenu = _texte_segments(segments).strip()
    if len(contenu) >= 90 or contenu.endswith(":") and len(contenu) > 60:
        return None
    if not all("b" in s[2] for s in textes):
        return None
    return "h3" if all("u" in s[2] for s in textes) else "h2"


def _tableau_docx(tableau, qn):
    corps = Noeud("tbody")
    for ligne in tableau.iter(qn("w:tr")):
        tr = Noeud("tr")
        for cellule in ligne.findall(qn("w:tc")):
            tcpr = cellule.find(qn("w:tcPr"))
            attributs = {}
            if tcpr is not None and tcpr.find(qn("w:gridSpan")) is not None:
                span = _entier(tcpr.find(qn("w:gridSpan")).get(qn("w:val")), 2, 20)
                if span:
                    attributs["colspan"] = span
            td = Noeud("td", attributs)
            for paragraphe in cellule.findall(qn("w:p")):
                segments = _sans_tabulations(_segments(paragraphe, qn))
                ppr = paragraphe.find(qn("w:pPr"))
                td.enfants.append(Noeud("p", _style(_alignement(ppr, qn)), _en_ligne(segments)))
            while len(td.enfants) > 1 and not td.enfants[-1].enfants:
                td.enfants.pop()
            if not td.enfants:
                td.enfants.append(Noeud("p"))
            tr.enfants.append(td)
        corps.enfants.append(tr)
    return Noeud("table", enfants=[corps])
