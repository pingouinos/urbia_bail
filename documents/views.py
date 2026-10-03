"""Remplacement des modèles Word par un administrateur (par exemple quand
Elodie retouche le texte d'un mandat)."""

import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from docxtpl import DocxTemplate

from comptes.roles import est_administrateur

from .generation import DOCX, MODELES_PAR_DEFAUT, ConversionImpossible, chemin_modele, convertir_pdf, generer_docx

MODELES = {
    "mandat_gestion.docx": "Mandat de gestion",
    "bail_habitation.docx": "Bail d'habitation (nu ou meublé)",
}
TAILLE_MAX = 10 * 1024 * 1024


def document(request, objet, modele, format_):
    """Réponse de téléchargement du document d'un objet (mandat, bail) en
    Word ou en PDF ; sans PDF disponible, retour à la fiche avec un message."""
    contenu = generer_docx(modele, objet.contexte_document())
    type_, extension = DOCX, "docx"
    if format_ == "pdf":
        try:
            contenu = convertir_pdf(contenu, modele)
        except ConversionImpossible:
            messages.error(request, "La conversion en PDF est indisponible pour le moment ; "
                                    "téléchargez la version Word.")
            return redirect(objet)
        type_, extension = "application/pdf", "pdf"
    reponse = HttpResponse(contenu, content_type=type_)
    reponse["Content-Disposition"] = f'attachment; filename="{objet.nom_fichier}.{extension}"'
    return reponse


def fichier_signe(objet):
    if not objet.fichier_signe:
        raise Http404
    type_, _ = mimetypes.guess_type(objet.fichier_signe.name)
    return FileResponse(objet.fichier_signe.open("rb"), content_type=type_ or "application/octet-stream")


def _verifier(request, nom):
    if not est_administrateur(request.user):
        raise PermissionDenied
    if nom not in MODELES:
        raise Http404


def _depose(nom):
    return Path(settings.MEDIA_ROOT) / "modeles" / nom


def _variables(source):
    return DocxTemplate(source).get_undeclared_template_variables()


def modeles(request):
    if not est_administrateur(request.user):
        raise PermissionDenied
    liste = [
        {"nom": nom, "libelle": libelle, "personnalise": _depose(nom).exists(),
         "variables": sorted(_variables(MODELES_PAR_DEFAUT / nom))}
        for nom, libelle in MODELES.items()
    ]
    return render(request, "documents/modeles.html", {"modeles": liste})


def telecharger(request, nom):
    _verifier(request, nom)
    return FileResponse(chemin_modele(nom).open("rb"), as_attachment=True, filename=nom, content_type=DOCX)


@require_POST
def deposer(request, nom):
    _verifier(request, nom)
    fichier = request.FILES.get("fichier")
    if not fichier or not fichier.name.lower().endswith(".docx") or fichier.size > TAILLE_MAX:
        messages.error(request, "Fournir un fichier Word (.docx) de 10 Mo au maximum.")
        return redirect("documents:modeles")
    try:
        variables = _variables(fichier)
    except Exception:
        messages.error(request, "Ce fichier Word n'a pas pu être lu, ou une balise {{ … }} est mal fermée.")
        return redirect("documents:modeles")
    inconnues = variables - _variables(MODELES_PAR_DEFAUT / nom)
    if inconnues:
        messages.error(request, "Balises inconnues : " + ", ".join(sorted(inconnues)) + ".")
        return redirect("documents:modeles")
    destination = _depose(nom)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fichier.seek(0)
    destination.write_bytes(fichier.read())
    messages.success(request, f"Nouveau modèle « {MODELES[nom]} » en service.")
    return redirect("documents:modeles")


@require_POST
def retablir(request, nom):
    _verifier(request, nom)
    _depose(nom).unlink(missing_ok=True)
    messages.success(request, f"Modèle d'origine « {MODELES[nom]} » rétabli.")
    return redirect("documents:modeles")
