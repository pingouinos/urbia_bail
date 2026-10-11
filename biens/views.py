import base64

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from baux.models import Bail
from candidatures.models import Candidature

from . import tableur
from .forms import BailleurForm, BienForm, ImportForm, MiseEnLocationForm
from .models import Bailleur, Bien

# Filtre « Location » de la liste des biens.
SITUATIONS = [
    ("a_louer", "À louer"), ("libre", "Libres ou sans bail"), ("preavis", "En préavis"), ("loue", "Loués"),
]
# Onglets de la fiche d'un bien.
ONGLETS = ("location", "informations", "candidatures", "baux", "mandats", "historique")

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
    situation = request.GET.get("situation")
    if situation in dict(SITUATIONS):
        aujourd_hui = timezone.localdate()
        en_place = Bail.objects.filter(bien=OuterRef("pk"), date_effet__lte=aujourd_hui).filter(
            Q(date_fin_effective__isnull=True) | Q(date_fin_effective__gt=aujourd_hui)
        )
        if situation == "a_louer":
            biens = biens.filter(mise_en_location_le__isnull=False)
        elif situation == "libre":
            biens = biens.filter(~Exists(en_place))
        else:
            biens = biens.filter(Exists(en_place.filter(conge_recu_le__isnull=situation == "loue")))
    return biens


def liste(request):
    biens = _biens_filtres(request).prefetch_related("baux__locataires")
    page = Paginator(biens, 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "biens/liste.html", {
        "page": page, "usages": Bien.Usage.choices, "situations": SITUATIONS,
        "parametres": parametres.urlencode(),
    })


def bien(request, pk):
    """Fiche d'un bien, en onglets. Le premier, « Location », suit la mise en
    location : situation, étape atteinte et prochaine action."""
    objet = get_object_or_404(Bien.objects.select_related("bailleur").prefetch_related("baux__locataires"), pk=pk)
    onglet = request.GET.get("onglet")
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
    return render(request, "biens/bien.html", {
        "bien": objet, "onglet": onglet, "situation": situation, "candidatures": candidatures,
        "toutes_candidatures": toutes, "retenue": retenue, "mandat": mandat, "mandats": mandats,
        "baux": sorted(objet.baux.all(), key=lambda bail: bail.date_effet, reverse=True),
        "etape": etape, "nouveau_bail": nouveau_bail,
        "historique": objet.history.select_related("history_user")[:20] if onglet == "historique" else [],
    })


def mettre_en_location(request, pk):
    """Ouvre (ou met à jour) la mise en location : date de disponibilité et
    loyer proposé. Elle se referme à la rédaction du bail."""
    objet = get_object_or_404(Bien.objects.select_related("bailleur").prefetch_related("baux__locataires"), pk=pk)
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
    return render(request, "biens/mise_en_location.html", {
        "bien": objet, "form": form, "situation": situation, "mandat": objet.mandat_en_cours,
    })


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
