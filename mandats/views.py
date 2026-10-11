from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font

from core.journal import noter
from proprietes.models import Propriete
from proprietes.tableur import sans_formule
from documents import views as documents

from .forms import MandatForm, SignatureForm
from .models import Mandat

MODELE = "mandat_gestion.docx"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _mandats_filtres(request):
    mandats = Mandat.objects.select_related("propriete")
    q = request.GET.get("q", "").strip()
    if q:
        filtre = Q(mandant__icontains=q) | Q(adresse_bien__icontains=q) | Q(propriete__ref_ics__icontains=q)
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
        "N°", "Date de signature", "Mandant", "Adresse du mandant", "Propriété", "Durée",
        "Fin", "Réf. ICS lot",
    ]
    feuille.append(entetes)
    for cellule in feuille[1]:
        cellule.font = Font(bold=True)
    noter(request, "export", "Registre des mandats")
    for mandat in _mandats_filtres(request).order_by("numero"):
        feuille.append([sans_formule(valeur) for valeur in (
            mandat.numero, mandat.date_signature, mandat.mandant,
            f"{mandat.mandant_adresse} {mandat.mandant_code_postal_ville}", mandat.adresse_bien,
            mandat.duree_texte, mandat.date_fin, mandat.propriete.ref_ics,
        )])
    for colonne, largeur in zip("ABCDEFGH", [6, 14, 36, 40, 40, 30, 12, 12]):
        feuille.column_dimensions[colonne].width = largeur
    reponse = HttpResponse(content_type=XLSX)
    nom = f"registre-mandats-{timezone.localdate():%Y-%m-%d}.xlsx"
    reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
    classeur.save(reponse)
    return reponse


def mandat(request, pk):
    objet = get_object_or_404(Mandat.objects.select_related("propriete__bailleur"), pk=pk)
    dossier = objet.dossier
    return render(request, "mandats/mandat.html", {
        "mandat": objet, "dossier": dossier,
        "nb_fournies": sum(1 for ligne in dossier if ligne["piece"] and not ligne["piece"].perime),
        "nb_manquantes": sum(1 for ligne in dossier if ligne["obligatoire"] and (not ligne["piece"] or ligne["piece"].perime)),
        "historique": objet.history.select_related("history_user")[:10],
        "form_signe": SignatureForm(instance=objet),
    })


def mandat_editer(request, pk=None):
    objet = get_object_or_404(Mandat, pk=pk) if pk else None
    if objet is None and request.method == "GET":
        if not request.GET.get("propriete"):
            # Premier temps : choisir le bien, pour préremplir le mandat.
            sous_mandat = Mandat.objects.filter(date_fin__isnull=True).values("propriete")
            proprietes = Propriete.objects.filter(actif=True).exclude(pk__in=sous_mandat)
            return render(request, "mandats/choisir_propriete.html", {
                "proprietes": proprietes.select_related("bailleur"),
            })
        propriete = get_object_or_404(Propriete.objects.select_related("bailleur"), pk=request.GET["propriete"])
        form = MandatForm(instance=Mandat.depuis_propriete(propriete))
    else:
        form = MandatForm(request.POST or None, instance=objet)
        if form.is_valid():
            creation = objet is None
            objet = form.save()
            immeuble = objet.propriete.immeuble if creation else None
            if immeuble:
                # Un mandat par lot : on revient à l'immeuble tant qu'il en reste à rédiger.
                restants = immeuble.lots.exclude(mandats__date_fin__isnull=True).count()
                if restants:
                    messages.success(request, f"Mandat n° {objet.numero} enregistré. Il reste {restants} "
                                              f"lot{'s' if restants > 1 else ''} sans mandat dans cet immeuble.")
                    return redirect(immeuble)
            messages.success(request, f"Mandat n° {objet.numero} enregistré."
                                      + (" Déposez les pièces du dossier ci-dessous." if creation else ""))
            return redirect(objet)
    return render(request, "mandats/mandat_form.html", {"form": form, "mandat": objet})


def telecharger(request, pk, format_):
    return documents.document(request, get_object_or_404(Mandat, pk=pk), MODELE, format_)


@require_POST
def deposer_signe(request, pk):
    objet = get_object_or_404(Mandat, pk=pk)
    form = SignatureForm(request.POST, request.FILES, instance=objet)
    if form.is_valid():
        form.save()
        messages.success(request, f"Signature du mandat enregistrée au {objet.date_signature:%d/%m/%Y}.")
    else:
        for erreurs in form.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
    return redirect(objet)


def fichier_signe(request, pk):
    return documents.fichier_signe(request, get_object_or_404(Mandat, pk=pk))
