from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from biens.models import Bien
from candidatures.models import Candidature
from documents import views as documents

from .forms import BailForm, FichierSigneForm, LocataireFormSet
from .models import Bail

MODELE = "bail_habitation.docx"


def liste(request):
    baux = Bail.objects.select_related("bien").prefetch_related("locataires")
    q = request.GET.get("q", "").strip()
    if q:
        baux = baux.filter(
            Q(bien__adresse__icontains=q) | Q(bien__ville__icontains=q) | Q(bien__ref_ics__icontains=q)
            | Q(locataires__nom__icontains=q) | Q(locataires__prenom__icontains=q)
        ).distinct()
    etat = request.GET.get("etat")
    if etat == "en_cours":
        baux = baux.filter(date_fin_effective__isnull=True)
    elif etat == "termines":
        baux = baux.filter(date_fin_effective__isnull=False)
    page = Paginator(baux, 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "baux/liste.html", {"page": page, "parametres": parametres.urlencode()})


def bail(request, pk):
    objet = get_object_or_404(Bail.objects.select_related("bien__bailleur", "mandat"), pk=pk)
    return render(request, "baux/bail.html", {
        "bail": objet,
        "historique": objet.history.select_related("history_user")[:10],
        "form_signe": FichierSigneForm(instance=objet),
    })


def bail_editer(request, pk=None):
    objet = get_object_or_404(Bail, pk=pk) if pk else None
    # Bail rédigé depuis une candidature retenue : logement, date d'entrée et
    # locataires viennent de la fiche du candidat.
    candidature = None
    pk_candidature = request.GET.get("candidature") or request.POST.get("candidature") or ""
    if objet is None and pk_candidature.isdigit():
        candidature = get_object_or_404(
            Candidature.objects.select_related("bien"), statut=Candidature.Statut.RETENUE, bail__isnull=True,
            pk=pk_candidature,
        )
    if objet is None and request.method == "GET":
        if not request.GET.get("bien") and not candidature:
            return render(request, "baux/choisir_bien.html", {
                "biens": Bien.objects.filter(actif=True, usage=Bien.Usage.HABITATION).select_related("bailleur"),
            })
        bien = candidature.bien if candidature else get_object_or_404(
            Bien.objects.select_related("bailleur"), pk=request.GET["bien"]
        )
        objet_initial = Bail.depuis_bien(bien)
        initial = []
        if candidature:
            objet_initial.date_effet = candidature.date_entree_souhaitee
            initial = [
                {champ: getattr(candidat, champ) for champ in (
                    "civilite", "nom", "prenom", "date_naissance", "lieu_naissance", "email", "telephone",
                )}
                for candidat in candidature.candidats.all()
            ]
        form = BailForm(instance=objet_initial)
        locataires = LocataireFormSet(instance=objet_initial, initial=initial)
        if initial:
            # Une ligne par candidat, sans ligne vide en plus.
            locataires.extra = max(len(initial) - locataires.min_num, 0)
    else:
        form = BailForm(request.POST or None, instance=objet)
        locataires = LocataireFormSet(request.POST or None, instance=form.instance)
        if request.method == "POST" and form.is_valid() and locataires.is_valid():
            if candidature and form.cleaned_data["bien"] != candidature.bien:
                form.add_error("bien", "La candidature porte sur un autre logement.")
            else:
                with transaction.atomic():
                    objet = form.save()
                    locataires.instance = objet
                    locataires.save()
                    if candidature:
                        candidature.bail = objet
                        candidature.save(update_fields=["bail", "modifie_le"])
                messages.success(request, "Bail enregistré.")
                return redirect(objet)
    return render(request, "baux/bail_form.html", {
        "form": form, "locataires": locataires, "bail": objet, "candidature": candidature,
    })


def telecharger(request, pk, format_):
    return documents.document(request, get_object_or_404(Bail, pk=pk), MODELE, format_)


@require_POST
def deposer_signe(request, pk):
    objet = get_object_or_404(Bail, pk=pk)
    form = FichierSigneForm(request.POST, request.FILES, instance=objet)
    if form.is_valid():
        form.save()
        messages.success(request, "Bail signé enregistré.")
    else:
        for erreur in form.errors.get("fichier_signe", []):
            messages.error(request, erreur)
    return redirect(objet)


def fichier_signe(request, pk):
    return documents.fichier_signe(get_object_or_404(Bail, pk=pk))
