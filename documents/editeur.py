"""Éditeur intégré des modèles : le corps du modèle Word en document
ProseMirror (l'éditeur TipTap de la page « Modifier le texte »), et le retour
au Word. Seuls les paragraphes modifiés sont réécrits : le reste du fichier
(mise en page, en-têtes, tableaux, styles) ne change pas.

Les balises docxtpl ne sont jamais du texte libre dans l'éditeur : une
donnée ({{ … }}) est une étiquette, une ligne de contrôle ({%p … %}) un
bloc qu'on ne peut ni modifier ni supprimer."""

import copy
import io
import re

from docx import Document
from docx.oxml.ns import qn

BALISE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")
CONTROLE = re.compile(r"^\s*\{%p?\s.*%\}\s*$")


class TexteInvalide(ValueError):
    pass


def _humain(expression):
    expression = expression.split("|")[0].strip()
    return expression.replace("_", " ").replace(".", " · ")


def libelle_balise(code):
    """Libellé lisible d'une balise : « bailleur email », « si … »."""
    interieur = code.strip()[2:-2].strip()
    if code.startswith("{{"):
        return _humain(interieur)
    mots = interieur.removeprefix("p ").strip().split(None, 1)
    mot, reste = (mots + [""])[:2]
    if mot == "if":
        return f"Seulement si : {_humain(reste)}"
    if mot == "elif":
        return f"Sinon si : {_humain(reste)}"
    if mot == "else":
        return "Sinon"
    if mot == "for":
        element, _, liste = reste.partition(" in ")
        return f"Pour chaque {_humain(element)} ({_humain(liste)})"
    if mot in ("endif", "endfor"):
        return "Fin du bloc"
    return interieur


def _elements(document):
    """Paragraphes et tableaux du corps, dans l'ordre : leur rang est
    l'identifiant que l'éditeur renvoie."""
    return [enfant for enfant in document.element.body if enfant.tag in (qn("w:p"), qn("w:tbl"))]


def _runs(paragraphe):
    return [enfant for enfant in paragraphe if enfant.tag == qn("w:r")]


def _texte_run(run):
    morceaux = []
    for enfant in run:
        if enfant.tag == qn("w:t"):
            morceaux.append(enfant.text or "")
        elif enfant.tag == qn("w:tab"):
            morceaux.append("\t")
        elif enfant.tag in (qn("w:br"), qn("w:cr")):
            morceaux.append("\n")
    return "".join(morceaux)


def _actif(rpr, balise):
    """Gras ou italique posé sur le run (« w:b » sans « w:val=0 »)."""
    if rpr is None:
        return False
    element = rpr.find(qn(balise))
    return element is not None and element.get(qn("w:val")) not in ("0", "false")


def _texte_paragraphe(paragraphe):
    return "".join(_texte_run(run) for run in _runs(paragraphe))


def _contenu(paragraphe):
    """Contenu ProseMirror d'un paragraphe : textes avec gras et italique,
    et une étiquette par balise, même coupée entre plusieurs runs."""
    caracteres = []
    for run in _runs(paragraphe):
        rpr = run.find(qn("w:rPr"))
        format_ = (_actif(rpr, "w:b"), _actif(rpr, "w:i"))
        caracteres.extend((c, format_) for c in _texte_run(run))
    texte = "".join(c for c, _ in caracteres)
    noeuds, position = [], 0

    def marques(format_):
        gras, italique = format_
        return [{"type": nom} for nom, actif in (("bold", gras), ("italic", italique)) if actif]

    def ajouter_texte(debut, fin):
        morceau, format_ = "", None
        for c, f in caracteres[debut:fin]:
            if f != format_ and morceau:
                noeuds.append({"type": "text", "text": morceau, "marks": marques(format_)})
                morceau = ""
            morceau, format_ = morceau + c, f
        if morceau:
            noeuds.append({"type": "text", "text": morceau, "marks": marques(format_)})

    for balise in BALISE.finditer(texte):
        ajouter_texte(position, balise.start())
        noeuds.append({"type": "donnee", "attrs": {"code": balise.group(), "libelle": libelle_balise(balise.group())},
                       "marks": marques(caracteres[balise.start()][1])})
        position = balise.end()
    ajouter_texte(position, len(texte))
    for noeud in noeuds:
        if not noeud["marks"]:
            del noeud["marks"]
    return noeuds


def _est_titre(paragraphe, texte):
    runs = [run for run in _runs(paragraphe) if _texte_run(run).strip()]
    return (bool(runs) and len(texte) < 90 and "{" not in texte
            and all(_actif(run.find(qn("w:rPr")), "w:b") for run in runs))


def lire(contenu, alertes=()):
    """Document ProseMirror du corps du modèle. ``alertes`` : (extrait,
    message) ; le paragraphe qui contient l'extrait porte le message."""
    document = Document(io.BytesIO(contenu))
    blocs = []
    for rang, element in enumerate(_elements(document)):
        if element.tag == qn("w:tbl"):
            cellules = [" ".join(t.text or "" for t in cellule.iter(qn("w:t"))).strip()
                        for cellule in element.iter(qn("w:tc"))]
            apercu = " · ".join(c for c in cellules if c)[:120]
            blocs.append({"type": "tableau", "attrs": {"rang": rang, "apercu": apercu}})
            continue
        texte = _texte_paragraphe(element)
        if CONTROLE.match(texte):
            blocs.append({"type": "controle", "attrs": {"rang": rang, "libelle": libelle_balise(texte.strip())}})
            continue
        alerte = next((message for extrait, message in alertes if extrait in " ".join(texte.split())), None)
        bloc = {"type": "paragraph", "attrs": {"rang": rang, "titre": _est_titre(element, texte), "alerte": alerte}}
        contenu_paragraphe = _contenu(element)
        if contenu_paragraphe:
            bloc["content"] = contenu_paragraphe
        blocs.append(bloc)
    return {"type": "doc", "content": blocs}


def _normaliser(noeuds):
    """Suite (texte, gras, italique) ou (balise, gras, italique), textes
    voisins de même format fusionnés : pour savoir si un paragraphe a changé."""
    resultat = []
    for noeud in noeuds or []:
        types = {marque.get("type") for marque in noeud.get("marks", [])}
        format_ = ("bold" in types, "italic" in types)
        if noeud.get("type") == "text":
            texte = noeud.get("text", "")
            if resultat and resultat[-1][0] == "texte" and resultat[-1][2:] == format_:
                resultat[-1] = ("texte", resultat[-1][1] + texte, *format_)
            elif texte:
                resultat.append(("texte", texte, *format_))
        elif noeud.get("type") == "donnee":
            resultat.append(("balise", noeud.get("attrs", {}).get("code", ""), *format_))
        elif noeud.get("type") == "hardBreak":
            resultat.append(("texte", "\n", *format_))
    return resultat


def _reecrire(paragraphe, segments):
    """Remplace les runs du paragraphe par les segments, dans la police du
    premier run d'origine ; la mise en forme du paragraphe reste."""
    modele = next(iter(_runs(paragraphe)), None)
    rpr_base = copy.deepcopy(modele.find(qn("w:rPr"))) if modele is not None else None
    if rpr_base is not None:
        for balise in ("w:b", "w:bCs", "w:i", "w:iCs"):
            for element in rpr_base.findall(qn(balise)):
                rpr_base.remove(element)
    for enfant in list(paragraphe):
        if enfant.tag != qn("w:pPr"):
            paragraphe.remove(enfant)
    for genre, texte, gras, italique in segments:
        run = paragraphe.makeelement(qn("w:r"), {})
        rpr = copy.deepcopy(rpr_base) if rpr_base is not None else run.makeelement(qn("w:rPr"), {})
        for actif, balise in ((gras, "w:b"), (italique, "w:i")):
            if actif:
                rpr.append(rpr.makeelement(qn(balise), {}))
        if len(rpr):
            run.append(rpr)
        for i, ligne in enumerate(texte.split("\n")):
            if i:
                run.append(run.makeelement(qn("w:br"), {}))
            t = run.makeelement(qn("w:t"), {})
            t.text = ligne
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            run.append(t)
        paragraphe.append(run)


def ecrire(contenu, document_editeur):
    """Modèle Word dont le corps suit le document de l'éditeur : paragraphes
    modifiés réécrits, nouveaux paragraphes créés sur le modèle du précédent,
    paragraphes retirés supprimés. Les blocs de contrôle et les tableaux ne
    peuvent pas disparaître."""
    document = Document(io.BytesIO(contenu))
    elements = _elements(document)
    origine = lire(contenu)["content"]
    blocs = document_editeur.get("content", []) if isinstance(document_editeur, dict) else []
    utilises, ordre, precedent = set(), [], None
    for bloc in blocs:
        rang = (bloc.get("attrs") or {}).get("rang")
        if not isinstance(rang, int) or not 0 <= rang < len(elements) or rang in utilises:
            rang = None
        if bloc.get("type") in ("controle", "tableau"):
            if rang is None or origine[rang]["type"] != bloc["type"]:
                raise TexteInvalide("Un bloc protégé du modèle a été modifié : recharger la page.")
            element = elements[rang]
        elif bloc.get("type") == "paragraph":
            if rang is not None and origine[rang]["type"] != "paragraph":
                rang = None
            segments = _normaliser(bloc.get("content"))
            if rang is not None:
                element = elements[rang]
                if segments != _normaliser(origine[rang].get("content")):
                    _reecrire(element, segments)
            else:
                source = precedent if precedent is not None and precedent.tag == qn("w:p") else next(
                    e for e in elements if e.tag == qn("w:p"))
                element = copy.deepcopy(source)
                _reecrire(element, segments)
        else:
            continue
        if rang is not None:
            utilises.add(rang)
        ordre.append(element)
        precedent = element
    for rang, bloc in enumerate(origine):
        if rang not in utilises and bloc["type"] != "paragraph":
            raise TexteInvalide("Les blocs « Seulement si », « Pour chaque » et les tableaux ne peuvent pas "
                                "être supprimés ici : déposer un fichier Word pour changer la structure.")
    corps = document.element.body
    for element in elements:
        corps.remove(element)
    fin = corps.find(qn("w:sectPr"))
    for element in ordre:
        if fin is not None:
            fin.addprevious(element)
        else:
            corps.append(element)
    flux = io.BytesIO()
    document.save(flux)
    return flux.getvalue()
