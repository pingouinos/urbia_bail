from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from biens.models import Bien
from candidatures.models import Candidature
from documents import views as documents

from .forms import BailForm, FichierSigneForm, LocataireFormSet, LogementForm, ProprietaireForm
from .models import Bail

MODELE = "bail_habitation.docx"
# Logements proposés à la fois à l'étape « Logement ».
NB_LOGEMENTS = 30


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
        # Bannière de fin de parcours, une seule fois après la création.
        "vient_d_etre_cree": request.session.pop("bail_cree", None) == objet.pk,
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
            return choisir_logement(request)
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
                    nouveau = objet is None
                    objet = form.save()
                    locataires.instance = objet
                    locataires.save()
                    if candidature:
                        candidature.bail = objet
                        candidature.save(update_fields=["bail", "modifie_le"])
                if nouveau:
                    request.session["bail_cree"] = objet.pk
                else:
                    messages.success(request, "Bail enregistré.")
                return redirect(objet)
    return render(request, "baux/bail_form.html", {
        "form": form, "locataires": locataires, "bail": objet, "candidature": candidature,
        "logement": form.instance.bien if form.instance.bien_id else None,
    })


def choisir_logement(request):
    """Étape 1 du parcours : le logement loué, retrouvé par son adresse, sa
    référence ou son propriétaire."""
    biens = Bien.objects.filter(actif=True, usage=Bien.Usage.HABITATION).select_related("bailleur")
    q = request.GET.get("q", "").strip()
    for mot in q.split():
        biens = biens.filter(
            Q(adresse__icontains=mot) | Q(complement__icontains=mot) | Q(code_postal__icontains=mot)
            | Q(ville__icontains=mot) | Q(ref_ics__icontains=mot) | Q(bailleur__nom__icontains=mot)
            | Q(bailleur__prenom__icontains=mot)
        )
    candidatures = Candidature.objects.filter(
        statut=Candidature.Statut.RETENUE, bail__isnull=True,
    ).select_related("bien").prefetch_related("candidats")
    return render(request, "baux/choisir_bien.html", {
        "q": q, "biens": biens[:NB_LOGEMENTS], "nb_biens": biens.count(), "candidatures": candidatures,
    })


def nouveau_logement(request):
    """Création d'un logement, et au besoin de son propriétaire, sans quitter
    le parcours de rédaction du bail."""
    logement = LogementForm(request.POST or None, prefix="logement")
    proprietaire = ProprietaireForm(request.POST or None, prefix="proprietaire")
    if request.method == "POST":
        valide = logement.is_valid()
        existant = logement.cleaned_data.get("proprietaire")
        if existant:
            # Les champs du nouveau propriétaire sont ignorés.
            proprietaire = ProprietaireForm(prefix="proprietaire")
        else:
            valide = proprietaire.is_valid() and valide
        if valide:
            with transaction.atomic():
                bien = logement.save(commit=False)
                bien.bailleur = existant or proprietaire.save()
                bien.save()
            messages.success(request, "Logement enregistré. Sa fiche pourra être complétée plus tard.")
            return redirect(f"{reverse('baux:creer')}?bien={bien.pk}")
    return render(request, "baux/nouveau_logement.html", {"logement": logement, "proprietaire": proprietaire})


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
