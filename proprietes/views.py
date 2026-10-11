import base64
import mimetypes

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from contrats.models import Bail
from candidatures.models import Candidature

from . import tableur
from .forms import (
    BailleurForm, ImmeubleForm, ImportForm, LotFormSet, LotsCommunsForm, MiseEnLocationForm, NouveauBailleurForm,
    PieceForm, ProprieteForm,
)
from .models import Bailleur, Immeuble, Piece, Propriete

# Ce qu'un nouveau lot reprend d'un lot déjà saisi dans le même immeuble.
COMMUNS_IMMEUBLE = (
    "bailleur", "usage", "type_habitat", "regime_juridique", "periode_construction", "chauffage", "eau_chaude",
    "syndic", "zone_tendue", "parties_communes",
)

# Filtre « Location » de la liste des biens.
SITUATIONS = [
    ("a_louer", "À louer"), ("libre", "Libres ou sans bail"), ("preavis", "En préavis"), ("loue", "Loués"),
]
# Onglets de la fiche d'un bien.
ONGLETS = ("location", "informations", "candidatures", "contrats", "documents", "historique")
# Anciens onglets, pour les favoris.
ANCIENS_ONGLETS = {"baux": "contrats", "mandats": "contrats"}

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CLE_SESSION_IMPORT = "import_proprietes"


def _proprietes_filtres(request):
    proprietes = Propriete.objects.select_related("bailleur", "immeuble")
    q = request.GET.get("q", "").strip()
    if q:
        proprietes = proprietes.filter(
            Q(ref_ics__icontains=q) | Q(adresse__icontains=q) | Q(complement__icontains=q)
            | Q(immeuble__nom__icontains=q)
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
    pieces = list(objet.pieces.select_related("depose_par"))
    return render(request, "proprietes/propriete.html", {
        "propriete": objet, "onglet": onglet, "situation": situation, "candidatures": candidatures,
        "toutes_candidatures": toutes, "retenue": retenue, "mandat": mandat, "mandats": mandats,
        "contrats": sorted(
            [*objet.baux.all(), *mandats], key=lambda contrat: contrat.debut or contrat.cree_le.date(), reverse=True,
        ),
        "etape": etape, "nouveau_bail": nouveau_bail,
        "pieces": pieces, "piece_dpe": next((piece for piece in pieces if piece.type == Piece.Type.DPE), None),
        "form_piece": PieceForm() if onglet == "documents" else None,
        "historique": objet.history.select_related("history_user")[:20] if onglet == "historique" else [],
    })


@require_POST
def piece_deposer(request, pk):
    objet = get_object_or_404(Propriete, pk=pk)
    form = PieceForm(request.POST, request.FILES)
    if form.is_valid():
        piece = form.save(commit=False)
        piece.propriete, piece.depose_par = objet, request.user
        piece.save()
        if piece.type == Piece.Type.DPE and piece.date_document and piece.date_document != objet.date_dpe:
            # La fiche suit le dernier DPE déposé.
            if not objet.date_dpe or piece.date_document > objet.date_dpe:
                objet.date_dpe = piece.date_document
                objet.save()
        messages.success(request, f"{piece.get_type_display()} déposé.")
    else:
        for erreurs in form.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
    return redirect(f"{objet.get_absolute_url()}?onglet=documents")


def piece(request, pk, piece_pk):
    objet = get_object_or_404(Piece, pk=piece_pk, propriete=pk)
    try:
        fichier = objet.fichier.open("rb")
    except FileNotFoundError:
        raise Http404
    type_, _ = mimetypes.guess_type(objet.fichier.name)
    reponse = FileResponse(fichier, content_type=type_ or "application/octet-stream")
    reponse["X-Content-Type-Options"] = "nosniff"
    return reponse


@require_POST
def piece_supprimer(request, pk, piece_pk):
    objet = get_object_or_404(Piece, pk=piece_pk, propriete=pk)
    objet.fichier.delete(save=False)
    objet.delete()
    messages.success(request, "Document supprimé.")
    return redirect(f"{reverse('proprietes:propriete', args=[pk])}?onglet=documents")


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
    if objet is None and request.GET.get("immeuble"):
        # Nouveau lot d'un immeuble : son adresse, et ce que ses lots ont en commun.
        immeuble = get_object_or_404(Immeuble, pk=request.GET["immeuble"])
        initial.update(immeuble=immeuble.pk, adresse=immeuble.adresse, code_postal=immeuble.code_postal,
                       ville=immeuble.ville, type_habitat=Propriete.TypeHabitat.COLLECTIF)
        voisin = immeuble.lots.order_by("-pk").first()
        if voisin:
            initial.update({champ: getattr(voisin, champ) for champ in COMMUNS_IMMEUBLE})
            initial["bailleur"] = voisin.bailleur_id
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


def immeuble_creer(request):
    """Un immeuble et ses lots en une fois : l'adresse et les caractéristiques
    communes se saisissent une seule fois, puis une ligne par lot."""
    donnees = request.POST or None
    form = ImmeubleForm(donnees, prefix="immeuble")
    communs = LotsCommunsForm(donnees, prefix="communs", initial={"bailleur": request.GET.get("bailleur")})
    nouveau_bailleur = NouveauBailleurForm(donnees, prefix="bailleur")
    lots = LotFormSet(donnees, prefix="lots")
    if request.method == "POST":
        valide = all([form.is_valid(), communs.is_valid(), lots.is_valid()])
        creer_bailleur = communs.is_valid() and not communs.cleaned_data.get("bailleur")
        if creer_bailleur:
            valide = nouveau_bailleur.is_valid() and valide
        else:
            nouveau_bailleur = NouveauBailleurForm(prefix="bailleur")
        if valide:
            with transaction.atomic():
                objet = form.save()
                valeurs = dict(communs.cleaned_data)
                if creer_bailleur:
                    valeurs["bailleur"] = nouveau_bailleur.save()
                nombre = 0
                for ligne in lots:
                    if not ligne.has_changed():
                        continue
                    lot = ligne.save(commit=False)
                    for champ, valeur in valeurs.items():
                        setattr(lot, champ, valeur)
                    lot.immeuble, lot.type_habitat = objet, Propriete.TypeHabitat.COLLECTIF
                    lot.adresse, lot.code_postal, lot.ville = objet.adresse, objet.code_postal, objet.ville
                    lot.save()
                    nombre += 1
            messages.success(request, f"Immeuble créé avec {nombre} lot{'s' if nombre > 1 else ''}. "
                                      "Chaque lot a sa fiche, à compléter avant de rédiger un bail.")
            return redirect(objet)
    return render(request, "proprietes/immeuble_form.html", {
        "form": form, "communs": communs, "nouveau_bailleur": nouveau_bailleur, "lots": lots,
    })


def immeuble(request, pk):
    objet = get_object_or_404(Immeuble, pk=pk)
    lots = list(objet.lots.select_related("bailleur").prefetch_related("baux__locataires").order_by("complement", "pk"))
    codes = [lot.situation.code for lot in lots]
    return render(request, "proprietes/immeuble.html", {
        "immeuble": objet, "lots": lots, "nb_loues": sum(code in ("loue", "preavis") for code in codes),
        "nb_a_louer": sum(lot.a_louer for lot in lots),
        "bailleurs": list(dict.fromkeys(lot.bailleur for lot in lots)),
    })


def immeuble_editer(request, pk):
    objet = get_object_or_404(Immeuble, pk=pk)
    form = ImmeubleForm(request.POST or None, instance=objet)
    if form.is_valid():
        with transaction.atomic():
            form.save().aligner_les_lots()
        messages.success(request, "Immeuble enregistré ; l'adresse de ses lots est à jour.")
        return redirect(objet)
    return render(request, "proprietes/immeuble_modifier.html", {"form": form, "immeuble": objet})


def bailleurs(request):
    """Les bailleurs se consultent dans les contacts, par leur catégorie."""
    parametres = request.GET.copy()
    parametres["categorie"] = "bailleur"
    return redirect(f"{reverse('contacts:liste')}?{parametres.urlencode()}")


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
