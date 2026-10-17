"""Préparation et consultation explicites des versions archivées."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from comptes.roles import est_administrateur
from core.journal import noter
from .archives import assurer_historique, creer, interdictions
from .generation import DOCX, ConversionImpossible, convertir_pdf
from .models import PdfArchive, VersionContrat


def contrat(type_):
    from contrats.models import Bail
    from mandats.models import Mandat
    if type_ not in ("bail", "mandat"):
        raise Http404
    return Bail if type_ == "bail" else Mandat


@require_POST
@transaction.atomic
def preparer(request, type_, pk):
    objet = get_object_or_404(contrat(type_).objects.select_for_update(), pk=pk)
    if objet.signe:
        if not est_administrateur(request.user):
            raise PermissionDenied
        assurer_historique(objet, request.user)
        messages.success(request, "Reprise historique conservée ; le document initial reste inconnu.")
    elif not objet.en_cours:
        raise PermissionDenied
    elif interdictions(objet):
        for obstacle in interdictions(objet):
            messages.error(request, obstacle)
        return redirect(objet)
    else:
        creer(objet, request.user, "preparation", "Version à transmettre aux parties pour signature.")
        messages.success(request, "Version à signer archivée. Téléchargez cette version pour la transmettre.")
    noter(request, "archive contractuelle", objet)
    return redirect(objet)


def reponse_version(request, version, format_):
    if format_ == "scan":
        if version.scan is None:
            raise Http404
        contenu = bytes(version.scan)
        # Le format a été contrôlé au dépôt ; la détection évite de faire confiance au nom.
        type_ = "application/pdf" if contenu.startswith(b"%PDF") else ("image/png" if contenu.startswith(b"\x89PNG") else "image/jpeg")
        extension = "pdf" if type_ == "application/pdf" else ("png" if type_ == "image/png" else "jpg")
    elif format_ == "docx":
        # Seules les archives d'avant le passage au HTML ont un Word.
        contenu, type_, extension = bytes(version.docx), DOCX, "docx"
        if not contenu.startswith(b"PK"):
            raise Http404
    elif format_ == "pdf":
        origine = version
        while origine.source_id and bytes(origine.source.docx) == bytes(origine.docx):
            origine = origine.source
        pdf = PdfArchive.objects.filter(version=origine).first()
        if not pdf:
            try:
                contenu = convertir_pdf(bytes(version.docx))
            except ConversionImpossible as erreur:
                messages.error(request, f"Le PDF n'a pas pu être produit ({erreur}) ; réessayez dans un instant.")
                return redirect(version.bail or version.mandat)
            pdf, _ = PdfArchive.objects.get_or_create(version=origine, defaults={"contenu": contenu})
        contenu, type_, extension = bytes(pdf.contenu), "application/pdf", "pdf"
    else:
        raise Http404
    noter(request, "document ouvert", f"Archive contractuelle {version.pk}", format_)
    response = HttpResponse(contenu, content_type=type_)
    response["Content-Disposition"] = f'attachment; filename="contrat-version-{version.pk}-{version.nature}.{extension}"'
    return response


def telecharger(request, pk, format_):
    return reponse_version(request, get_object_or_404(VersionContrat, pk=pk), format_)
