"""Modèles Word des documents, et leur remplacement par un administrateur :
reprise depuis un Google Doc où Elodie retouche le texte, ou dépôt d'un
fichier Word. Un modèle n'est mis en service qu'après vérification de ses
balises et un remplissage d'essai ; la version remplacée reste disponible."""

import io
import mimetypes
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from docxtpl import DocxTemplate
from jinja2 import TemplateSyntaxError

from comptes.roles import est_administrateur

from . import google
from .generation import DOCX, MODELES_PAR_DEFAUT, ConversionImpossible, chemin_modele, convertir_pdf, generer_docx
from .models import Modele

# Modèles remplaçables : libellé, et objet dont le dernier modifié sert au
# remplissage d'essai.
MODELES = {
    "mandat_gestion.docx": ("Mandat de gestion", "mandats.Mandat"),
    "bail_habitation.docx": ("Bail d'habitation (nu ou meublé)", "baux.Bail"),
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


def _precedent(nom):
    return Path(settings.MEDIA_ROOT) / "modeles" / "precedent" / nom


def _variables(source):
    return DocxTemplate(source).get_undeclared_template_variables()


def _objet_essai(nom):
    return apps.get_model(MODELES[nom][1]).objects.order_by("-modifie_le").first()


def verifier_modele(nom, contenu):
    """Raison pour laquelle le modèle ne peut pas servir, ou None."""
    try:
        variables = _variables(io.BytesIO(contenu))
    except TemplateSyntaxError as erreur:
        return f"Une balise est mal écrite ({erreur.message}) : vérifier les {{{{ … }}}} et {{% … %}}."
    except Exception:
        return "Ce fichier Word n'a pas pu être lu."
    inconnues = variables - _variables(MODELES_PAR_DEFAUT / nom)
    if inconnues:
        return "Balises inconnues : " + ", ".join(sorted(inconnues)) + "."
    objet = _objet_essai(nom)
    if objet is not None:
        # Remplissage d'essai avec le dernier document de ce type.
        try:
            DocxTemplate(io.BytesIO(contenu)).render(objet.contexte_document(), autoescape=True)
        except Exception as erreur:
            return f"Le remplissage d'essai échoue ({erreur})."
    return None


def mettre_en_service(nom, contenu, origine, utilisateur):
    """Remplace le modèle en service ; l'ancien devient la version
    précédente. Sans contenu, revient au modèle d'origine."""
    depose, precedent = _depose(nom), _precedent(nom)
    precedent.parent.mkdir(parents=True, exist_ok=True)
    ancien = chemin_modele(nom).read_bytes()
    if contenu is None:
        depose.unlink(missing_ok=True)
    else:
        depose.write_bytes(contenu)
    precedent.write_bytes(ancien)
    Modele.objects.update_or_create(nom=nom, defaults={
        "origine": origine, "mis_en_service_le": timezone.now(), "mis_en_service_par": utilisateur,
    })


def modeles(request):
    if not est_administrateur(request.user):
        raise PermissionDenied
    etats = {modele.nom: modele for modele in Modele.objects.select_related("mis_en_service_par")}
    liste = [
        {"nom": nom, "libelle": libelle, "etat": etats.get(nom) or Modele(nom=nom),
         "personnalise": _depose(nom).exists(), "precedent": _precedent(nom).exists(),
         "essai": _objet_essai(nom) is not None, "variables": sorted(_variables(MODELES_PAR_DEFAUT / nom))}
        for nom, (libelle, _) in MODELES.items()
    ]
    return render(request, "documents/modeles.html", {"modeles": liste})


def telecharger(request, nom):
    _verifier(request, nom)
    return FileResponse(chemin_modele(nom).open("rb"), as_attachment=True, filename=nom, content_type=DOCX)


def essai(request, nom):
    """Dernier document de ce type, produit avec le modèle en service."""
    _verifier(request, nom)
    objet = _objet_essai(nom)
    if objet is None:
        messages.error(request, f"Aucun {MODELES[nom][0].lower()} enregistré pour faire un essai.")
        return redirect("documents:modeles")
    return document(request, objet, nom, "pdf")


@require_POST
def reprendre(request, nom):
    _verifier(request, nom)
    libelle = MODELES[nom][0]
    modele, _ = Modele.objects.get_or_create(nom=nom)
    lien = request.POST.get("lien_google", "").strip()
    if lien != modele.lien_google:
        if google.identifiant(lien) is None:
            messages.error(request, "Coller le lien du Google Doc, de la forme https://docs.google.com/document/d/….")
            return redirect("documents:modeles")
        modele.lien_google = lien
        modele.save(update_fields=["lien_google"])
    try:
        contenu = google.telecharger(modele.lien_google)
    except google.LectureImpossible as erreur:
        messages.error(request, f"« {libelle} » n'a pas été repris. {erreur}")
        return redirect("documents:modeles")
    raison = verifier_modele(nom, contenu)
    if raison:
        messages.error(request, f"« {libelle} » n'a pas été mis en service. {raison}")
        return redirect("documents:modeles")
    mettre_en_service(nom, contenu, Modele.Origine.GOOGLE, request.user)
    messages.success(request, f"« {libelle} » repris de Google Docs et mis en service. "
                              "Ouvrez le document d'essai pour vérifier la mise en page.")
    return redirect("documents:modeles")


@require_POST
def deposer(request, nom):
    _verifier(request, nom)
    fichier = request.FILES.get("fichier")
    if not fichier or not fichier.name.lower().endswith(".docx") or fichier.size > TAILLE_MAX:
        messages.error(request, "Fournir un fichier Word (.docx) de 10 Mo au maximum.")
        return redirect("documents:modeles")
    contenu = fichier.read()
    raison = verifier_modele(nom, contenu)
    if raison:
        messages.error(request, raison)
        return redirect("documents:modeles")
    mettre_en_service(nom, contenu, Modele.Origine.DEPOT, request.user)
    messages.success(request, f"Nouveau modèle « {MODELES[nom][0]} » en service.")
    return redirect("documents:modeles")


@require_POST
def precedent(request, nom):
    _verifier(request, nom)
    if not _precedent(nom).exists():
        raise Http404
    mettre_en_service(nom, _precedent(nom).read_bytes(), Modele.Origine.PRECEDENT, request.user)
    messages.success(request, f"Version précédente de « {MODELES[nom][0]} » remise en service.")
    return redirect("documents:modeles")


@require_POST
def retablir(request, nom):
    _verifier(request, nom)
    mettre_en_service(nom, None, Modele.Origine.ORIGINE, request.user)
    messages.success(request, f"Modèle d'origine « {MODELES[nom][0]} » rétabli.")
    return redirect("documents:modeles")
