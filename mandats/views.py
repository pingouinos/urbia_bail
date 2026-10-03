import mimetypes

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font

from biens.models import Bien
from documents.generation import DOCX, ConversionImpossible, convertir_pdf, generer_docx

from .forms import FichierSigneForm, MandatForm
from .models import Mandat

MODELE = "mandat_gestion.docx"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _mandats_filtres(request):
    mandats = Mandat.objects.select_related("bien")
    q = request.GET.get("q", "").strip()
    if q:
        filtre = Q(mandant__icontains=q) | Q(adresse_bien__icontains=q) | Q(bien__ref_ics__icontains=q)
        if q.isdigit():
            filtre |= Q(numero=int(q))
        mandats = mandats.filter(filtre)
    etat = request.GET.get("etat")
    if etat == "en_cours":
        mandats = mandats.filter(date_fin__isnull=True)
    elif etat == "termines":
        mandats = mandats.filter(date_fin__isnull=False)
    return mandats


def registre(request):
    page = Paginator(_mandats_filtres(request), 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "mandats/registre.html", {"page": page, "parametres": parametres.urlencode()})


def registre_export(request):
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Registre des mandats"
    entetes = [
        "N°", "Date de signature", "Mandant", "Adresse du mandant", "Bien", "Durée",
        "Fin", "Réf. ICS lot",
    ]
    feuille.append(entetes)
    for cellule in feuille[1]:
        cellule.font = Font(bold=True)
    for mandat in _mandats_filtres(request).order_by("numero"):
        feuille.append([
            mandat.numero, mandat.date_signature, mandat.mandant,
            f"{mandat.mandant_adresse} {mandat.mandant_code_postal_ville}", mandat.adresse_bien,
            mandat.duree_texte, mandat.date_fin, mandat.bien.ref_ics,
        ])
    for colonne, largeur in zip("ABCDEFGH", [6, 14, 36, 40, 40, 30, 12, 12]):
        feuille.column_dimensions[colonne].width = largeur
    reponse = HttpResponse(content_type=XLSX)
    nom = f"registre-mandats-{timezone.localdate():%Y-%m-%d}.xlsx"
    reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
    classeur.save(reponse)
    return reponse


def mandat(request, pk):
    objet = get_object_or_404(Mandat.objects.select_related("bien__bailleur"), pk=pk)
    return render(request, "mandats/mandat.html", {
        "mandat": objet,
        "historique": objet.history.select_related("history_user")[:10],
        "form_signe": FichierSigneForm(instance=objet),
    })


def mandat_editer(request, pk=None):
    objet = get_object_or_404(Mandat, pk=pk) if pk else None
    if objet is None and request.method == "GET":
        if not request.GET.get("bien"):
            # Premier temps : choisir le bien, pour préremplir le mandat.
            sous_mandat = Mandat.objects.filter(date_fin__isnull=True).values("bien")
            biens = Bien.objects.filter(actif=True).exclude(pk__in=sous_mandat)
            return render(request, "mandats/choisir_bien.html", {
                "biens": biens.select_related("bailleur"),
            })
        bien = get_object_or_404(Bien.objects.select_related("bailleur"), pk=request.GET["bien"])
        form = MandatForm(instance=Mandat.depuis_bien(bien))
    else:
        form = MandatForm(request.POST or None, instance=objet)
        if form.is_valid():
            objet = form.save()
            messages.success(request, f"Mandat n° {objet.numero} enregistré.")
            return redirect(objet)
    return render(request, "mandats/mandat_form.html", {"form": form, "mandat": objet})


def telecharger(request, pk, format_):
    objet = get_object_or_404(Mandat, pk=pk)
    contenu = generer_docx(MODELE, objet.contexte_document())
    type_, extension = DOCX, "docx"
    if format_ == "pdf":
        try:
            contenu = convertir_pdf(contenu, f"mandat-{objet.numero}.docx")
        except ConversionImpossible:
            messages.error(request, "La conversion en PDF est indisponible pour le moment ; "
                                    "téléchargez la version Word.")
            return redirect(objet)
        type_, extension = "application/pdf", "pdf"
    reponse = HttpResponse(contenu, content_type=type_)
    reponse["Content-Disposition"] = f'attachment; filename="{objet.nom_fichier}.{extension}"'
    return reponse


@require_POST
def deposer_signe(request, pk):
    objet = get_object_or_404(Mandat, pk=pk)
    form = FichierSigneForm(request.POST, request.FILES, instance=objet)
    if form.is_valid():
        form.save()
        messages.success(request, "Mandat signé enregistré.")
    else:
        for erreur in form.errors.get("fichier_signe", []):
            messages.error(request, erreur)
    return redirect(objet)


def fichier_signe(request, pk):
    objet = get_object_or_404(Mandat, pk=pk)
    if not objet.fichier_signe:
        raise Http404
    type_, _ = mimetypes.guess_type(objet.fichier_signe.name)
    return FileResponse(objet.fichier_signe.open("rb"), content_type=type_ or "application/octet-stream")
