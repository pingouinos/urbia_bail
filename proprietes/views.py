import base64

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from contrats.models import Bail
from candidatures.models import Candidature

from . import tableur
from .forms import BailleurForm, ImportForm, MiseEnLocationForm, NouveauBailleurForm, ProprieteForm
from .models import Bailleur, Propriete

# Filtre « Location » de la liste des biens.
SITUATIONS = [
    ("a_louer", "À louer"), ("libre", "Libres ou sans bail"), ("preavis", "En préavis"), ("loue", "Loués"),
]
# Onglets de la fiche d'un bien.
ONGLETS = ("location", "informations", "candidatures", "contrats", "historique")
# Anciens onglets, pour les favoris.
ANCIENS_ONGLETS = {"baux": "contrats", "mandats": "contrats"}

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CLE_SESSION_IMPORT = "import_proprietes"


def _proprietes_filtres(request):
    proprietes = Propriete.objects.select_related("bailleur")
    q = request.GET.get("q", "").strip()
    if q:
        proprietes = proprietes.filter(
            Q(ref_ics__icontains=q) | Q(adresse__icontains=q) | Q(complement__icontains=q)
            | Q(ville__icontains=q) | Q(code_postal__startswith=q)
            | Q(bailleur__nom__icontains=q) | Q(bailleur__prenom__icontains=q)
        )
    usage = request.GET.get("usage")
    if usage in Propriete.Usage.values:
        proprietes = proprietes.filter(usage=usage)
    if request.GET.get("inactifs") != "1":
        proprietes = proprietes.filter(actif=True)
    situation = request.GET.get("situation")
    if situation in dict(SITUATIONS):
        aujourd_hui = timezone.localdate()
        en_place = Bail.objects.filter(propriete=OuterRef("pk"), date_effet__lte=aujourd_hui).filter(
            Q(date_fin_effective__isnull=True) | Q(date_fin_effective__gt=aujourd_hui)
        )
        if situation == "a_louer":
            proprietes = proprietes.filter(mise_en_location_le__isnull=False)
        elif situation == "libre":
            proprietes = proprietes.filter(~Exists(en_place))
        else:
            proprietes = proprietes.filter(Exists(en_place.filter(conge_recu_le__isnull=situation == "loue")))
    return proprietes


def liste(request):
    proprietes = _proprietes_filtres(request).prefetch_related("baux__locataires")
    page = Paginator(proprietes, 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "proprietes/liste.html", {
        "page": page, "usages": Propriete.Usage.choices, "situations": SITUATIONS,
        "parametres": parametres.urlencode(),
    })


def propriete(request, pk):
    """Fiche d'un bien, en onglets. Le premier, « Location », suit la mise en
    location : situation, étape atteinte et prochaine action."""
    objet = get_object_or_404(Propriete.objects.select_related("bailleur").prefetch_related("baux__locataires"), pk=pk)
    onglet = request.GET.get("onglet")
    onglet = ANCIENS_ONGLETS.get(onglet, onglet)
    if onglet not in ONGLETS:
        onglet = "location"
    situation = objet.situation
    toutes = list(objet.candidatures.select_related("bail").prefetch_related("candidats"))
    candidatures = [
        c for c in toutes
        if not c.bail_id and c.statut in (Candidature.Statut.A_ETUDIER, Candidature.Statut.RETENUE)
    ]
    retenue = next((c for c in candidatures if c.statut == Candidature.Statut.RETENUE), None)
    mandats = list(objet.mandats.order_by("-numero"))
    mandat = next((m for m in mandats if m.date_fin is None), None)
    # Étape de la mise en location : mandat, mise en location, candidatures,
    # bail, signature, entrée. Rien à suivre quand le logement est loué sans
    # départ annoncé.
    nouveau_bail = situation.a_venir or (situation.bail if situation.code == "loue" else None)
    if situation.code == "loue" and situation.bail.date_signature:
        etape = None
    elif nouveau_bail:
        etape = 6 if nouveau_bail.date_signature else 5
    elif retenue:
        etape = 4
    elif objet.a_louer or candidatures:
        etape = 3
    elif mandat:
        etape = 2
    else:
        etape = 1
    return render(request, "proprietes/propriete.html", {
        "propriete": objet, "onglet": onglet, "situation": situation, "candidatures": candidatures,
        "toutes_candidatures": toutes, "retenue": retenue, "mandat": mandat, "mandats": mandats,
        "contrats": sorted(
            [*objet.baux.all(), *mandats], key=lambda contrat: contrat.debut or contrat.cree_le.date(), reverse=True,
        ),
        "etape": etape, "nouveau_bail": nouveau_bail,
        "historique": objet.history.select_related("history_user")[:20] if onglet == "historique" else [],
    })


def mettre_en_location(request, pk):
    """Ouvre (ou met à jour) la mise en location : date de disponibilité et
    loyer proposé. Elle se referme à la rédaction du bail."""
    objet = get_object_or_404(Propriete.objects.select_related("bailleur").prefetch_related("baux__locataires"), pk=pk)
    situation = objet.situation
    if request.method == "POST" and request.POST.get("action") == "retirer":
        objet.mise_en_location_le = objet.disponible_le = None
        objet.save(update_fields=["mise_en_location_le", "disponible_le", "modifie_le"])
        messages.success(request, "Le logement n'est plus proposé à la location.")
        return redirect(objet)
    initial = {}
    if not objet.disponible_le:
        depart = situation.bail and (situation.bail.date_fin_effective or situation.bail.date_depart_prevue)
        initial["disponible_le"] = max(
            depart + timezone.timedelta(days=1) if depart else timezone.localdate(), timezone.localdate()
        )
    form = MiseEnLocationForm(request.POST or None, instance=objet, initial=initial)
    if request.method == "POST" and form.is_valid():
        objet = form.save(commit=False)
        objet.mise_en_location_le = objet.mise_en_location_le or timezone.localdate()
        objet.save()
        messages.success(request, "Logement mis en location : enregistrez les candidatures au fur et à mesure.")
        return redirect(objet)
    return render(request, "proprietes/mise_en_location.html", {
        "propriete": objet, "form": form, "situation": situation, "mandat": objet.mandat_en_cours,
    })


def propriete_editer(request, pk=None):
    objet = get_object_or_404(Propriete, pk=pk) if pk else None
    initial = {}
    if objet is None and request.GET.get("bailleur"):
        initial["bailleur"] = request.GET["bailleur"]
    form = ProprieteForm(request.POST or None, instance=objet, initial=initial)
    # À la création, un bailleur absent de la liste se saisit sur la même page.
    nouveau_bailleur = NouveauBailleurForm(request.POST or None, prefix="bailleur") if objet is None else None
    if request.method == "POST":
        valide = form.is_valid()
        creer_bailleur = nouveau_bailleur is not None and not form.cleaned_data.get("bailleur")
        if creer_bailleur:
            valide = nouveau_bailleur.is_valid() and valide
        elif nouveau_bailleur is not None:
            # Bailleur existant choisi : les champs du nouveau sont ignorés.
            nouveau_bailleur = NouveauBailleurForm(prefix="bailleur")
        if valide:
            with transaction.atomic():
                objet = form.save(commit=False)
                if creer_bailleur:
                    objet.bailleur = nouveau_bailleur.save()
                objet.save()
            messages.success(request, "Fiche enregistrée.")
            return redirect(objet)
    return render(request, "proprietes/propriete_form.html", {
        "form": form, "propriete": objet, "nouveau_bailleur": nouveau_bailleur,
    })


def bailleurs(request):
    q = request.GET.get("q", "").strip()
    liste_ = Bailleur.objects.annotate(nb_proprietes=Count("proprietes"))
    if q:
        liste_ = liste_.filter(Q(nom__icontains=q) | Q(prenom__icontains=q) | Q(ref_ics__icontains=q))
    page = Paginator(liste_, 50).get_page(request.GET.get("page"))
    return render(request, "proprietes/bailleurs.html", {"page": page, "q": q})


def bailleur(request, pk):
    objet = get_object_or_404(Bailleur, pk=pk)
    return render(request, "proprietes/bailleur.html", {"bailleur": objet, "proprietes": objet.proprietes.all()})


def bailleur_editer(request, pk=None):
    objet = get_object_or_404(Bailleur, pk=pk) if pk else None
    form = BailleurForm(request.POST or None, instance=objet)
    if form.is_valid():
        objet = form.save()
        messages.success(request, "Bailleur enregistré.")
        return redirect(objet)
    return render(request, "proprietes/bailleur_form.html", {"form": form, "bailleur": objet})


def modele(request):
    reponse = HttpResponse(tableur.classeur(), content_type=XLSX)
    reponse["Content-Disposition"] = 'attachment; filename="modele-proprietes-urbia-gestion.xlsx"'
    return reponse


def exporter(request):
    proprietes = _proprietes_filtres(request)
    reponse = HttpResponse(tableur.classeur(proprietes), content_type=XLSX)
    nom = f"proprietes-urbia-gestion-{timezone.localdate():%Y-%m-%d}.xlsx"
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
    return render(request, "proprietes/import.html", {"form": form, "rapport": rapport})


@require_POST
def importer_confirmer(request):
    """Étape 2 : import réel du fichier simulé juste avant."""
    depot = request.session.pop(CLE_SESSION_IMPORT, None)
    if not depot:
        messages.error(request, "Aucun fichier en attente : déposez-le à nouveau.")
        return redirect("proprietes:importer")
    rapport = tableur.importer(depot["nom"], base64.b64decode(depot["contenu"]))
    messages.success(
        request,
        f"Import terminé : {rapport.crees} bien(s) créé(s), {rapport.mis_a_jour} mis à jour, "
        f"{rapport.bailleurs_crees} bailleur(s) créé(s), {len(rapport.erreurs)} ligne(s) ignorée(s).",
    )
    return redirect("proprietes:liste")
