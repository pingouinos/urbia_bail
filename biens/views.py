import base64

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import tableur
from .forms import BailleurForm, BienForm, ImportForm
from .models import Bailleur, Bien

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CLE_SESSION_IMPORT = "import_biens"


def _biens_filtres(request):
    biens = Bien.objects.select_related("bailleur")
    q = request.GET.get("q", "").strip()
    if q:
        biens = biens.filter(
            Q(ref_ics__icontains=q) | Q(adresse__icontains=q) | Q(complement__icontains=q)
            | Q(ville__icontains=q) | Q(code_postal__startswith=q)
            | Q(bailleur__nom__icontains=q) | Q(bailleur__prenom__icontains=q)
        )
    usage = request.GET.get("usage")
    if usage in Bien.Usage.values:
        biens = biens.filter(usage=usage)
    if request.GET.get("inactifs") != "1":
        biens = biens.filter(actif=True)
    return biens


def liste(request):
    page = Paginator(_biens_filtres(request), 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "biens/liste.html", {
        "page": page, "usages": Bien.Usage.choices, "parametres": parametres.urlencode(),
    })


def bien(request, pk):
    objet = get_object_or_404(Bien.objects.select_related("bailleur"), pk=pk)
    historique = objet.history.select_related("history_user")[:10]
    return render(request, "biens/bien.html", {"bien": objet, "historique": historique})


def bien_editer(request, pk=None):
    objet = get_object_or_404(Bien, pk=pk) if pk else None
    initial = {}
    if objet is None and request.GET.get("bailleur"):
        initial["bailleur"] = request.GET["bailleur"]
    form = BienForm(request.POST or None, instance=objet, initial=initial)
    if form.is_valid():
        objet = form.save()
        messages.success(request, "Fiche enregistrée.")
        return redirect(objet)
    return render(request, "biens/bien_form.html", {"form": form, "bien": objet})


def bailleurs(request):
    q = request.GET.get("q", "").strip()
    liste_ = Bailleur.objects.annotate(nb_biens=Count("biens"))
    if q:
        liste_ = liste_.filter(Q(nom__icontains=q) | Q(prenom__icontains=q) | Q(ref_ics__icontains=q))
    page = Paginator(liste_, 50).get_page(request.GET.get("page"))
    return render(request, "biens/bailleurs.html", {"page": page, "q": q})


def bailleur(request, pk):
    objet = get_object_or_404(Bailleur, pk=pk)
    return render(request, "biens/bailleur.html", {"bailleur": objet, "biens": objet.biens.all()})


def bailleur_editer(request, pk=None):
    objet = get_object_or_404(Bailleur, pk=pk) if pk else None
    form = BailleurForm(request.POST or None, instance=objet)
    if form.is_valid():
        objet = form.save()
        messages.success(request, "Bailleur enregistré.")
        return redirect(objet)
    return render(request, "biens/bailleur_form.html", {"form": form, "bailleur": objet})


def modele(request):
    reponse = HttpResponse(tableur.classeur(), content_type=XLSX)
    reponse["Content-Disposition"] = 'attachment; filename="modele-biens-urbia-gestion.xlsx"'
    return reponse


def exporter(request):
    biens = _biens_filtres(request)
    reponse = HttpResponse(tableur.classeur(biens), content_type=XLSX)
    nom = f"biens-urbia-gestion-{timezone.localdate():%Y-%m-%d}.xlsx"
    reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
    return reponse


def importer(request):
    """Étape 1 : dépôt du fichier et simulation, sans rien enregistrer."""
    form = ImportForm(request.POST or None, request.FILES or None)
    rapport = None
    if form.is_valid():
        fichier = form.cleaned_data["fichier"]
        contenu = fichier.read()
        try:
            rapport = tableur.importer(fichier.name, contenu, simulation=True)
        except Exception:  # fichier illisible ou corrompu
            form.add_error("fichier", "Impossible de lire ce fichier. Vérifiez qu'il s'agit bien d'un .xlsx ou d'un .csv.")
        else:
            request.session[CLE_SESSION_IMPORT] = {
                "nom": fichier.name, "contenu": base64.b64encode(contenu).decode(),
            }
    return render(request, "biens/import.html", {"form": form, "rapport": rapport})


@require_POST
def importer_confirmer(request):
    """Étape 2 : import réel du fichier simulé juste avant."""
    depot = request.session.pop(CLE_SESSION_IMPORT, None)
    if not depot:
        messages.error(request, "Aucun fichier en attente : déposez-le à nouveau.")
        return redirect("biens:importer")
    rapport = tableur.importer(depot["nom"], base64.b64decode(depot["contenu"]))
    messages.success(
        request,
        f"Import terminé : {rapport.crees} bien(s) créé(s), {rapport.mis_a_jour} mis à jour, "
        f"{rapport.bailleurs_crees} bailleur(s) créé(s), {len(rapport.erreurs)} ligne(s) ignorée(s).",
    )
    return redirect("biens:liste")
