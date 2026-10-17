import datetime as dt

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font

from comptes.roles import est_administrateur
from contrats import echeances
from core.journal import noter
from core.processus import navigation, processus_mandat, sections_etapes
from proprietes.models import Propriete
from proprietes.tableur import sans_formule
from documents import views as documents
from documents.archives import verrouiller, assurer_historique, creer, motif_correction, signer

from .forms import FinMandatForm, MandatForm, SignatureForm
from .models import Mandat

MODELE = "mandat_gestion"
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


def mandat_editer(request):
    """Rédaction d'un mandat. Un mandat déjà rédigé se modifie sur la page
    de ses étapes (`etapes`)."""
    objet = None
    if request.method == "GET":
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
        form = MandatForm(request.POST)
        if form.is_valid():
            objet = form.save()
            immeuble = objet.propriete.immeuble
            if immeuble:
                # Un mandat par lot : on revient à l'immeuble tant qu'il en reste à rédiger.
                restants = immeuble.lots.exclude(mandats__date_fin__isnull=True).count()
                if restants:
                    messages.success(request, f"Mandat n° {objet.numero} enregistré. Il reste {restants} "
                                              f"lot{'s' if restants > 1 else ''} sans mandat dans cet immeuble.")
                    return redirect(immeuble)
            messages.success(request, f"Mandat n° {objet.numero} enregistré : il est prêt à faire signer.")
            return redirect(vers_etapes(objet))
    return render(request, "mandats/mandat_form.html", {"form": form})


def vers_etapes(mandat, etape=None):
    """La page du mandat, à l'étape demandée ou à l'étape en cours."""
    url = mandat.get_absolute_url()
    if etape:
        return f"{url}?etape={etape}#etape-{etape}"
    return f"{url}#etape-{min(processus_mandat(mandat).rang, 5)}"


def etapes_ouvrables(mandat, rang):
    """Les étapes qui se rouvrent d'un crayon ou d'une flèche de la frise :
    l'étape en cours ; la rédaction tant que le mandat n'est pas signé ; la
    signature tant qu'elle se retire (dossier incomplet, aucun bail signé) ;
    tant que le mandat court, le dossier, une pièce pouvant se déposer ou se
    remplacer avant comme après la signature, et la fin. Terminé, plus rien."""
    if mandat.date_fin:
        return set()
    ouvrables = {rang, 3, 5}
    if not mandat.signe:
        ouvrables.add(1)
    elif mandat.signature_retirable:
        ouvrables.add(2)
    return ouvrables


@verrouiller(Mandat)
def etapes(request, pk):
    """La page d'un mandat, façon PLM et comme celle du bail : la frise en
    haut avec ses flèches, une section par étape dessous, un crayon sur les
    étapes qui se modifient encore, un cadenas une fois le mandat signé. Les
    champs du mandat lui-même s'enregistrent ici ; la signature, les pièces
    et la fin ont leur propre vue."""
    objet = get_object_or_404(Mandat.objects.select_related("propriete__bailleur"), pk=pk)
    administrateur = est_administrateur(request.user)
    form = MandatForm(request.POST or None, instance=objet)
    if request.method == "POST":
        if (objet.signe or not objet.en_cours) and not administrateur:
            messages.error(request, "Ce mandat est signé : pour le corriger, revenir d'abord à la signature.")
            return redirect(vers_etapes(objet))
        motif = ""
        if objet.signe or not objet.en_cours:
            try:
                motif = motif_correction(request)
            except ValidationError as erreur:
                messages.error(request, " ".join(erreur.messages))
                return redirect(vers_etapes(objet))
            assurer_historique(objet, request.user)
        if form.is_valid():
            form.save()
            if motif:
                creer(objet, request.user, "correction", motif)
                noter(request, "correction contrat", objet, motif)
            messages.success(request, f"Mandat n° {objet.numero} enregistré.")
            return redirect(vers_etapes(objet))
    dossier = objet.dossier
    manquantes = [ligne for ligne in dossier if ligne["obligatoire"] and (not ligne["piece"] or ligne["piece"].perime)]
    processus = processus_mandat(objet)
    ouvrables = etapes_ouvrables(objet, processus.rang)
    # Signé et la signature ne se retire plus : seul un administrateur
    # corrige la rédaction ; terminé, seul un administrateur corrige la fin.
    corrigeables = set()
    if administrateur and objet.signe and not objet.signature_retirable:
        corrigeables.add(1)
    if administrateur and objet.date_fin:
        corrigeables.add(5)
    demandee = request.GET.get("etape", "")
    demandee = int(demandee) if demandee.isdigit() else None
    if form.is_bound:
        ouverte = 1
    elif demandee in ouvrables or demandee in corrigeables:
        ouverte = demandee
    else:
        ouverte = processus.rang if processus.rang <= 5 else None
    precedente, suivante = navigation(processus, ouverte, ouvrables)
    return render(request, "mandats/etapes.html", {
        "mandat": objet, "processus": processus, "ouverte": ouverte,
        "sections": sections_etapes(processus, ouverte, ouvrables, {1, 2} if objet.signe else (), corrigeables),
        "correction": ouverte == 1 and objet.signe, "precedente": precedente, "suivante": suivante,
        "annuler": vers_etapes(objet),
        "form": form, "form_signe": SignatureForm(instance=objet), "form_fin": FinMandatForm(instance=objet),
        "dossier": dossier, "nb_manquantes": len(manquantes),
        "administrateur": administrateur, "historique": objet.history.select_related("history_user")[:10],
        **echeance_du_mandat(objet),
    })


def echeance_du_mandat(mandat):
    """Fin de la période en cours d'un mandat signé, et la lettre de la loi
    Chatel quand le mandant est protégé."""
    periode = echeances.periode_mandat(mandat)
    if not periode:
        return {}
    fin, reconduction = periode
    return {
        "periode_fin": fin, "periode_suivante": fin + dt.timedelta(days=1), "reconduction": reconduction,
        "chatel": reconduction and echeances.protege_par_chatel(mandat),
        "chatel_du": echeances.ajouter_mois(fin, -3), "chatel_limite": echeances.ajouter_mois(fin, -1),
        "chatel_envoyee": echeances.lettre_chatel_envoyee(mandat, fin),
        "chatel_ouverte": echeances.echeance_mandat(mandat) is not None,
        "aujourd_hui": timezone.localdate(),
    }


@require_POST
@verrouiller(Mandat)
def information_reconduction(request, pk):
    """Lettre de fin de période envoyée au mandant (loi Chatel)."""
    from contrats.views import _date_postee

    objet = get_object_or_404(Mandat, pk=pk)
    objet.information_reconduction_le = _date_postee(request, "information_reconduction_le")
    objet.save(update_fields=["information_reconduction_le", "modifie_le"])
    noter(request, "lettre de reconduction", objet, f"Envoyée le {objet.information_reconduction_le:%d/%m/%Y}")
    messages.success(request, f"Lettre notée comme envoyée le {objet.information_reconduction_le:%d/%m/%Y}.")
    return redirect(f"{objet.get_absolute_url()}#echeance")


@require_POST
@verrouiller(Mandat)
def retirer_signature(request, pk):
    """‹ Du dossier à la signature : la date de signature est retirée (les
    archives restent), et le mandat redevient modifiable. Plus possible une fois
    le mandat en gestion."""
    objet = get_object_or_404(Mandat, pk=pk)
    if not objet.signature_retirable:
        messages.error(request, "Le mandat est en gestion : la signature ne se retire plus.")
    else:
        assurer_historique(objet, request.user)
        noter(request, "signature retirée", objet, "Retour à la rédaction ; archives conservées.")
        objet.date_signature = None
        objet.fichier_signe = ""
        objet.save(update_fields=["date_signature", "fichier_signe", "modifie_le"])
        messages.success(request, "Signature retirée : le mandat est de nouveau modifiable.")
        return redirect(vers_etapes(objet, 1))
    return redirect(vers_etapes(objet))


@require_POST
@verrouiller(Mandat)
def fin(request, pk):
    """Fin du mandat (résiliation ou échéance). Une fois terminé, seul un
    administrateur en corrige la date."""
    objet = get_object_or_404(Mandat, pk=pk)
    if objet.date_fin and not est_administrateur(request.user):
        messages.error(request, "Ce mandat est terminé : seul un administrateur peut corriger sa date de fin.")
        return redirect(vers_etapes(objet))
    motif = ""
    if request.method == "POST" and objet.date_fin:
        try:
            motif = motif_correction(request)
        except ValidationError as erreur:
            messages.error(request, " ".join(erreur.messages))
            return redirect(vers_etapes(objet))
    source = assurer_historique(objet, request.user) if motif else None
    form = FinMandatForm(request.POST, instance=objet)
    if form.is_valid():
        form.save()
        if motif:
            creer(objet, request.user, "correction_fin", motif, source=source)
        noter(request, "fin contrat", objet, motif or "Fin enregistrée")
        messages.success(request, f"Fin du mandat enregistrée au {objet.date_fin:%d/%m/%Y}.")
    else:
        for erreurs in form.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
    return redirect(vers_etapes(objet))


def telecharger(request, pk, format_):
    return documents.document(request, get_object_or_404(Mandat, pk=pk), MODELE, format_)


@require_POST
@verrouiller(Mandat)
def deposer_signe(request, pk):
    objet = get_object_or_404(Mandat, pk=pk)
    form = SignatureForm(request.POST, request.FILES, instance=Mandat.objects.get(pk=pk))
    if form.is_valid():
        try:
            signer(request, objet, form)
        except ValidationError as erreur:
            messages.error(request, " ".join(erreur.messages))
            return redirect(vers_etapes(objet))
        messages.success(request, f"Signature du mandat enregistrée au {objet.date_signature:%d/%m/%Y}.")
    else:
        for erreurs in form.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
    return redirect(vers_etapes(objet))


def fichier_signe(request, pk):
    return documents.fichier_signe(request, get_object_or_404(Mandat, pk=pk))
