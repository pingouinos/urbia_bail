
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from proprietes.models import Propriete
from candidatures.models import Candidature
from comptes.roles import est_administrateur
from contacts.models import Contact
from core.fichiers import servir
from core.journal import noter
from core.processus import Processus, navigation, processus_bail, sections_etapes, sous_etapes_sortie
from mandats.models import Mandat
from documents import views as documents
from documents.archives import verrouiller, assurer_historique, creer, motif_correction, signer

from django.utils import timezone

from .forms import (
    BailForm, CongeForm, EdlSortieForm, EntreeForm, FinBailForm, LocataireFormSet, LogementForm, ProprietaireForm,
    RestitutionForm, SignatureForm, initial_fin,
)
from .echeances import assurance_due, euros, prochaine_date_revision, revision_due
from .models import Bail, IndiceIRL, RevisionLoyer

MODELE = "bail_habitation"
# Logements proposés à la fois à l'étape « Logement ».
NB_LOGEMENTS = 30


TYPES = [("bail", "Baux"), ("mandat", "Mandats")]


def liste(request):
    """Tous les contrats, baux et mandats confondus, du plus récent au plus
    ancien."""
    q = request.GET.get("q", "").strip()
    type_ = request.GET.get("type")
    etat = request.GET.get("etat")
    baux = Bail.objects.select_related("propriete").prefetch_related("locataires")
    mandats = Mandat.objects.select_related("propriete")
    if q:
        baux = baux.filter(
            Q(propriete__adresse__icontains=q) | Q(propriete__ville__icontains=q)
            | Q(propriete__numero_lot__icontains=q)
            | Q(locataires__nom__icontains=q) | Q(locataires__prenom__icontains=q)
        ).distinct()
        filtre = (
            Q(mandant__icontains=q) | Q(propriete__adresse__icontains=q) | Q(propriete__ville__icontains=q)
            | Q(propriete__numero_lot__icontains=q)
        )
        if q.isdigit():
            filtre |= Q(numero=int(q))
        mandats = mandats.filter(filtre)
    if etat == "en_cours":
        baux, mandats = baux.filter(date_fin_effective__isnull=True), mandats.filter(date_fin__isnull=True)
    elif etat == "termines":
        baux, mandats = baux.filter(date_fin_effective__isnull=False), mandats.filter(date_fin__isnull=False)
    contrats = [*(baux if type_ != "mandat" else ()), *(mandats if type_ != "bail" else ())]
    contrats.sort(key=lambda contrat: contrat.debut or contrat.cree_le.date(), reverse=True)
    page = Paginator(contrats, 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "contrats/liste.html", {
        "page": page, "parametres": parametres.urlencode(), "types": TYPES,
    })


def choisir(request):
    """« Nouveau contrat » : un mandat avec le bailleur, ou un bail avec le locataire."""
    return render(request, "contrats/choisir.html")


def bail_editer(request):
    """Rédaction d'un bail : logement, puis locataires et conditions. Un bail
    déjà rédigé se modifie sur la page de ses étapes (`etapes`)."""
    # Bail rédigé depuis une candidature retenue : logement, date d'entrée et
    # locataires viennent de la fiche du candidat.
    candidature = None
    pk_candidature = request.GET.get("candidature") or request.POST.get("candidature") or ""
    if pk_candidature.isdigit():
        candidature = get_object_or_404(
            Candidature.objects.select_related("propriete"), statut=Candidature.Statut.RETENUE, bail__isnull=True,
            pk=pk_candidature,
        )
    if request.method == "GET":
        if not request.GET.get("propriete") and not candidature:
            return choisir_logement(request)
        propriete = candidature.propriete if candidature else get_object_or_404(
            Propriete.objects.select_related("bailleur"), pk=request.GET["propriete"]
        )
        empechement = Bail.empechement(propriete)
        if empechement and empechement[0].en_cours and not empechement[0].conge_recu_le:
            messages.error(request, empechement[1])
            return redirect(empechement[0])
        objet_initial = Bail.depuis_propriete(propriete)
        initial = []
        if candidature:
            objet_initial.date_effet = candidature.date_entree_souhaitee
            initial = [
                {
                    **{champ: getattr(candidat, champ) for champ in (
                        "civilite", "nom", "prenom", "date_naissance", "lieu_naissance", "email", "telephone",
                    )},
                    "garants": "\n".join(garant.ligne_bail for garant in candidat.garants.all()),
                }
                for candidat in candidature.candidats.prefetch_related("garants")
            ]
        form = BailForm(instance=objet_initial)
        locataires = LocataireFormSet(instance=objet_initial, initial=initial)
        if initial:
            # Une ligne par candidat, sans ligne vide en plus.
            locataires.extra = max(len(initial) - locataires.min_num, 0)
    else:
        form = BailForm(request.POST or None)
        locataires = LocataireFormSet(request.POST or None, instance=form.instance)
        if request.method == "POST" and form.is_valid() and locataires.is_valid():
            if candidature and form.cleaned_data["propriete"] != candidature.propriete:
                form.add_error("propriete", "La candidature porte sur un autre logement.")
            else:
                with transaction.atomic():
                    objet = form.save()
                    locataires.instance = objet
                    locataires.save()
                    if candidature:
                        candidature.bail = objet
                        candidature.save(update_fields=["bail", "modifie_le"])
                    if objet.propriete.a_louer:
                        # Le bail rédigé clôt la mise en location.
                        objet.propriete.mise_en_location_le = objet.propriete.disponible_le = None
                        objet.propriete.save(update_fields=["mise_en_location_le", "disponible_le", "modifie_le"])
                request.session["bail_cree"] = objet.pk
                return redirect(objet)
    return render(request, "contrats/bail_form.html", {
        "form": form, "locataires": locataires, "candidature": candidature,
        "logement": form.instance.propriete if form.instance.propriete_id else None,
    })


def vers_etapes(bail, etape=None):
    """La page du bail, à l'étape demandée ou à l'étape en cours."""
    url = bail.get_absolute_url()
    if etape:
        return f"{url}?etape={etape}#etape-{etape}"
    return f"{url}#etape-{min(processus_bail(bail).rang, 7)}"


def etapes_ouvrables(bail, rang):
    """Les étapes qui se rouvrent d'un crayon ou d'une flèche de la frise :
    l'étape en cours ; le bail tant qu'il n'est pas signé ; la signature
    tant que le locataire n'est pas entré (on peut alors la retirer) ; la
    sortie dès la location en cours, quand un congé arrive ; l'entrée, pour
    corriger son état des lieux, tant que la location court. Terminé (dépôt
    restitué), plus rien."""
    if bail.termine:
        return set()
    ouvrables = {rang}
    if rang == 6 and bail.edl_entree_le:
        ouvrables.add(5)
    if not bail.signe:
        ouvrables.add(3)
        if rang <= 4:
            ouvrables.add(4)
    elif bail.signature_retirable:
        ouvrables.add(4)
    if rang == 6:
        ouvrables.add(7)
    return ouvrables


@verrouiller(Bail)
def etapes(request, pk):
    """La page d'un bail, façon PLM : la frise en haut avec ses flèches, une
    section par étape dessous. L'étape en cours est ouverte avec ses champs,
    les étapes atteintes sont grisées en lecture seule, avec un crayon tant
    qu'elles se modifient et un cadenas une fois le bail signé. Les champs
    du bail lui-même s'enregistrent ici ; la signature et le départ ont leur
    propre vue."""
    objet = get_object_or_404(Bail.objects.select_related("propriete__bailleur", "mandat"), pk=pk)
    administrateur = est_administrateur(request.user)
    form = BailForm(request.POST or None, instance=objet)
    locataires = LocataireFormSet(request.POST or None, instance=objet)
    if request.method == "POST":
        if (objet.signe or not objet.en_cours) and not administrateur:
            messages.error(request, "Ce bail est signé : pour le corriger, revenir d'abord à la signature.")
            return redirect(vers_etapes(objet))
        motif = ""
        if objet.signe or not objet.en_cours:
            try:
                motif = motif_correction(request)
            except ValidationError as erreur:
                messages.error(request, " ".join(erreur.messages))
                return redirect(vers_etapes(objet))
            assurer_historique(objet, request.user)
        if form.is_valid() and locataires.is_valid():
            with transaction.atomic():
                form.save()
                locataires.save()
                if motif:
                    creer(objet, request.user, "correction", motif)
                    noter(request, "correction contrat", objet, motif)
            messages.success(request, "Bail enregistré.")
            return redirect(vers_etapes(objet))
    processus = processus_bail(objet)
    ouvrables = etapes_ouvrables(objet, processus.rang)
    # Une fois le locataire entré, seul un administrateur corrige le bail signé.
    corrigeables = {3} if administrateur and objet.signe and processus.rang >= 6 else set()
    if administrateur and objet.termine:
        corrigeables.add(7)
    demandee = request.GET.get("etape", "")
    demandee = int(demandee) if demandee.isdigit() else None
    if form.is_bound:
        ouverte = 3
    elif demandee in ouvrables or demandee in corrigeables:
        ouverte = demandee
    else:
        ouverte = processus.rang if processus.rang <= 7 else None
    precedente, suivante = navigation(processus, ouverte, ouvrables)
    return render(request, "contrats/etapes.html", {
        "bail": objet, "processus": processus, "ouverte": ouverte,
        "sections": sections_etapes(processus, ouverte, ouvrables, {3, 4} if objet.signe else (), corrigeables),
        "correction": ouverte == 3 and objet.signe, "precedente": precedente, "suivante": suivante,
        "annuler": vers_etapes(objet),
        "form": form, "locataires": locataires,
        "form_signe": SignatureForm(instance=objet),
        "form_fin": CongeForm(instance=objet, initial=initial_fin(objet)),
        "form_entree": EntreeForm(instance=objet),
        "form_edl_sortie": EdlSortieForm(instance=objet),
        "form_restitution": RestitutionForm(instance=objet),
        "sortie": frise_sortie(objet, ouverte == 7, request.GET.get("sous", "")),
        "candidature": Candidature.objects.filter(bail=objet).prefetch_related("candidats").first(),
        # Message de fin de parcours, une seule fois après la création.
        "vient_d_etre_cree": request.session.pop("bail_cree", None) == objet.pk,
        "administrateur": administrateur, "historique": objet.history.select_related("history_user")[:10],
        **suivi_location(objet),
    })


def suivi_location(bail):
    """Révision du loyer et attestation d'assurance, sur la fiche d'un bail
    signé en cours."""
    if not bail.signe or bail.date_fin_effective:
        return {}
    revisions = list(bail.revisions.select_related("cree_par"))
    derniere = revisions[0] if revisions else None
    revision, assurance = revision_due(bail), assurance_due(bail)
    courrier = derniere if derniere and derniere.statut == RevisionLoyer.Statut.APPLIQUEE \
        and (timezone.localdate() - derniere.cree_le.date()).days <= 60 else None
    return {
        "suivi": True,
        # Une action à faire (ou le courrier à envoyer) remonte le cadre en haut de la fiche.
        "suivi_en_haut": bool((revision and not revision.gelee) or assurance or courrier),
        "revision": revision, "revisions": revisions,
        "prochaine_revision": prochaine_date_revision(bail),
        # Courrier au locataire, proposé dans les deux mois qui suivent la révision.
        "courrier": courrier, "assurance_due": assurance,
        "aujourd_hui": timezone.localdate(),
    }


@require_POST
@verrouiller(Bail)
def retirer_signature(request, pk):
    """‹ De l'entrée à la signature : la date de signature est retirée (les
    archives restent), et le bail redevient modifiable. Plus possible une fois le
    locataire entré."""
    objet = get_object_or_404(Bail, pk=pk)
    if not objet.signature_retirable:
        messages.error(request, "Le locataire est entré dans les lieux : la signature ne se retire plus.")
    else:
        assurer_historique(objet, request.user)
        noter(request, "signature retirée", objet, "Retour à la rédaction ; archives conservées.")
        objet.date_signature = None
        objet.fichier_signe = ""
        objet.save(update_fields=["date_signature", "fichier_signe", "modifie_le"])
        messages.success(request, "Signature retirée : le bail est de nouveau modifiable.")
        return redirect(vers_etapes(objet, 3))
    return redirect(vers_etapes(objet))


@require_POST
@verrouiller(Bail)
def annuler_conge(request, pk):
    """‹ De la sortie à la location en cours : le congé est annulé, tant que
    le locataire n'est pas parti."""
    objet = get_object_or_404(Bail.objects.select_related("propriete"), pk=pk)
    if not objet.conge_annulable:
        messages.error(request, "Il n'y a pas de congé à annuler sur ce bail.")
        return redirect(vers_etapes(objet))
    objet.conge_origine, objet.conge_recu_le, objet.date_depart_prevue = "", None, None
    objet.save(update_fields=["conge_origine", "conge_recu_le", "date_depart_prevue", "modifie_le"])
    suite = " Le logement reste proposé à la location : la retirer depuis sa fiche si besoin." if objet.propriete.a_louer else ""
    messages.success(request, "Congé annulé : la location continue." + suite)
    return redirect(vers_etapes(objet))


def frise_sortie(bail, ouverte, demandee):
    """La petite frise de la sortie (congé, état des lieux, dépôt) et la
    sous-étape ouverte ; un point se rouvre d'un clic tant qu'il se modifie."""
    numero, possibles = sous_ouverte(bail, demandee)
    etapes = sous_etapes_sortie(bail)
    page = bail.get_absolute_url()
    for n, etape in enumerate(etapes, 1):
        etape.lien = f"{page}?etape=7&sous={n}#etape-7" if ouverte and n in possibles else ""
    return {"processus": Processus("Sortie", etapes, bail.sous_etape_sortie), "ouverte": numero}


def sous_ouverte(bail, demandee):
    """La sous-étape de la sortie ouverte sur la page : celle en cours, ou la
    précédente quand elle se modifie encore (le congé tant que les clés ne sont
    pas rendues, l'état des lieux de sortie tant que le dépôt n'est pas rendu).
    Un bail terminé ne rouvre que la restitution, pour un administrateur."""
    courante = min(bail.sous_etape_sortie, 3)
    possibles = {courante}
    if courante == 2 and bail.conge_recu_le:
        possibles.add(1)
    if courante == 3 and not bail.termine:
        possibles.add(2)
    demandee = int(demandee) if demandee.isdigit() else None
    return (demandee if demandee in possibles else courante), possibles


def _erreurs(request, form):
    for erreurs in form.errors.values():
        for erreur in erreurs:
            messages.error(request, erreur)


@require_POST
@verrouiller(Bail)
def entree(request, pk):
    """L'état des lieux d'entrée clôt l'entrée dans les lieux."""
    objet = get_object_or_404(Bail, pk=pk)
    if not objet.signe or not objet.en_cours:
        messages.error(request, "L'état des lieux d'entrée s'enregistre sur un bail signé, en cours.")
        return redirect(vers_etapes(objet))
    form = EntreeForm(request.POST, request.FILES, instance=objet)
    if not form.is_valid():
        _erreurs(request, form)
        return redirect(vers_etapes(objet, 5))
    objet = form.save()
    noter(request, "état des lieux d'entrée", objet, f"le {objet.edl_entree_le:%d/%m/%Y}")
    messages.success(request, f"État des lieux d'entrée enregistré au {objet.edl_entree_le:%d/%m/%Y}.")
    return redirect(vers_etapes(objet))


@require_POST
@verrouiller(Bail)
def edl_sortie(request, pk):
    """Remise des clés et état des lieux de sortie : le logement redevient
    libre, et le délai de restitution du dépôt court."""
    objet = get_object_or_404(Bail.objects.select_related("propriete"), pk=pk)
    if objet.termine:
        messages.error(request, "Ce bail est terminé : seul un administrateur peut corriger sa fin.")
        return redirect(vers_etapes(objet))
    premiere = objet.en_cours
    form = EdlSortieForm(request.POST, request.FILES, instance=objet)
    if not form.is_valid():
        _erreurs(request, form)
        return redirect(f"{vers_etapes(objet, 7).split('#')[0]}&sous=2#etape-7")
    objet = form.save()
    noter(request, "fin contrat", objet, f"Clés rendues le {objet.date_fin_effective:%d/%m/%Y}")
    if objet.restitution_attendue:
        messages.success(request, f"Clés rendues le {objet.date_fin_effective:%d/%m/%Y} : logement libre. "
                                  f"Dépôt à restituer avant le {objet.restitution_avant:%d/%m/%Y}.")
    else:
        messages.success(request, f"Départ enregistré : logement libre depuis le {objet.date_fin_effective:%d/%m/%Y}.")
    propriete = objet.propriete
    if premiere and not propriete.a_louer and not propriete.situation.a_venir:
        # Le départ ouvre la mise en location du logement.
        return redirect("proprietes:mettre_en_location", pk=propriete.pk)
    return redirect(vers_etapes(objet))


@require_POST
@verrouiller(Bail)
def restitution(request, pk):
    """La restitution du dépôt de garantie termine le bail. Une fois terminé,
    seul un administrateur la corrige, avec un motif."""
    objet = get_object_or_404(Bail, pk=pk)
    if not objet.date_fin_effective:
        messages.error(request, "Le dépôt se restitue une fois les clés rendues.")
        return redirect(vers_etapes(objet))
    motif = ""
    if objet.termine:
        if not est_administrateur(request.user):
            messages.error(request, "Ce bail est terminé : seul un administrateur peut corriger sa fin.")
            return redirect(vers_etapes(objet))
        try:
            motif = motif_correction(request)
        except ValidationError as erreur:
            messages.error(request, " ".join(erreur.messages))
            return redirect(vers_etapes(objet, 7))
    form = RestitutionForm(request.POST, request.FILES, instance=objet)
    if not form.is_valid():
        _erreurs(request, form)
        return redirect(vers_etapes(objet, 7))
    objet = form.save()
    noter(request, "restitution du dépôt", objet, motif or f"{objet.depot_a_rendre} € rendus le {objet.depot_restitue_le:%d/%m/%Y}")
    messages.success(request, f"Dépôt restitué le {objet.depot_restitue_le:%d/%m/%Y} : le bail est terminé.")
    return redirect(vers_etapes(objet))


FICHIERS_EDL = {"edl_entree": "État des lieux d'entrée", "edl_sortie": "État des lieux de sortie",
                "depot_justificatif": "Justificatif des retenues"}


def fichier_edl(request, pk, champ):
    if champ not in FICHIERS_EDL:
        raise Http404
    objet = get_object_or_404(Bail, pk=pk)
    noter(request, "document ouvert", f"{FICHIERS_EDL[champ]} · {objet}")
    return servir(getattr(objet, champ))


def choisir_logement(request):
    """Étape 1 du parcours : le logement loué, retrouvé par son adresse, sa
    référence ou son propriétaire."""
    proprietes = Propriete.objects.filter(actif=True, usage=Propriete.Usage.HABITATION).select_related("bailleur")
    q = request.GET.get("q", "").strip()
    for mot in q.split():
        proprietes = proprietes.filter(
            Q(adresse__icontains=mot) | Q(complement__icontains=mot) | Q(code_postal__icontains=mot)
            | Q(ville__icontains=mot) | Q(numero_lot__icontains=mot) | Q(bailleur__nom__icontains=mot)
            | Q(bailleur__prenom__icontains=mot)
        )
    candidatures = Candidature.objects.filter(
        statut=Candidature.Statut.RETENUE, bail__isnull=True,
    ).select_related("propriete").prefetch_related("candidats")
    return render(request, "contrats/choisir_propriete.html", {
        "q": q, "proprietes": proprietes[:NB_LOGEMENTS], "nb_proprietes": proprietes.count(), "candidatures": candidatures,
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
                propriete = logement.save(commit=False)
                propriete.bailleur = existant or proprietaire.save()
                propriete.save()
            messages.success(request, "Logement enregistré. Sa fiche pourra être complétée plus tard.")
            return redirect(f"{reverse('contrats:creer')}?propriete={propriete.pk}")
    return render(request, "contrats/nouveau_logement.html", {"logement": logement, "proprietaire": proprietaire})


@verrouiller(Bail)
def fin(request, pk):
    """Départ du locataire en une page : le congé reçu et la fin du préavis
    sont proposés d'avance, puis le départ effectif une fois le préavis fini."""
    objet = get_object_or_404(Bail.objects.select_related("propriete"), pk=pk)
    if request.method == "POST" and objet.termine and not est_administrateur(request.user):
        messages.error(request, "Ce bail est terminé : seul un administrateur peut corriger sa fin.")
        return redirect(vers_etapes(objet))
    motif = ""
    if request.method == "POST" and objet.termine:
        try:
            motif = motif_correction(request)
        except ValidationError as erreur:
            messages.error(request, " ".join(erreur.messages))
            return redirect(vers_etapes(objet))
    source = assurer_historique(objet, request.user) if motif else None
    # Depuis la page du bail, seul le congé est envoyé : la remise des clés a
    # sa propre sous-étape.
    classe = CongeForm if request.POST.get("etapes") else FinBailForm
    form = classe(request.POST or None, instance=objet, initial=initial_fin(objet))
    if request.method == "POST" and form.is_valid():
        objet = form.save()
        if motif:
            creer(objet, request.user, "correction_fin", motif, source=source)
        noter(request, "fin contrat", objet, motif or "Fin enregistrée")
        if objet.date_fin_effective:
            messages.success(request, f"Départ enregistré : logement libre depuis le {objet.date_fin_effective:%d/%m/%Y}.")
        elif objet.conge_recu_le:
            messages.success(request, f"Congé enregistré : logement libre le {objet.date_depart_prevue:%d/%m/%Y}.")
        else:
            messages.success(request, "Bail enregistré.")
        propriete = objet.propriete
        if (objet.conge_recu_le or objet.date_fin_effective) and not propriete.a_louer and not propriete.situation.a_venir:
            # Le départ ouvre la mise en location du logement.
            return redirect("proprietes:mettre_en_location", pk=propriete.pk)
        return redirect(vers_etapes(objet) if request.POST.get("etapes") else propriete)
    return render(request, "contrats/fin.html", {"bail": objet, "form": form})


@require_POST
@verrouiller(Bail)
def supprimer(request, pk):
    """Supprime un bail rédigé par erreur ou resté sans suite. Un bail signé
    engage les parties : il se termine par le départ du locataire. Un contrat
    signé ou archivé est conservé, y compris pour les administrateurs."""
    objet = get_object_or_404(Bail.objects.select_related("propriete"), pk=pk)
    if objet.signe or objet.versions_document.exists():
        messages.error(request, "Ce bail possède des archives contractuelles : conservez-le et utilisez la fin du bail.")
        return redirect(objet)
    propriete, signe = objet.propriete, objet.signe
    candidature = Candidature.objects.filter(bail=objet).first()
    with transaction.atomic():
        if objet.fichier_signe:
            objet.fichier_signe.delete(save=False)
        objet.delete()
        Contact.effacer_orphelins()
        suite = ""
        if candidature:
            # La candidature retenue retrouve son étape : « rédiger le bail ».
            suite = " La candidature retenue est conservée : le bail peut être rédigé à nouveau."
        elif not signe and propriete.situation.code in ("libre", "sans_bail") and not propriete.situation.a_venir:
            # Le bail avait clos la mise en location : elle reprend.
            propriete.mise_en_location_le = timezone.localdate()
            propriete.disponible_le = max(objet.date_effet, timezone.localdate())
            propriete.save(update_fields=["mise_en_location_le", "disponible_le", "modifie_le"])
            suite = " Le logement est de nouveau proposé à la location."
    noter(request, "suppression", f"Bail · {propriete}", "signé" if signe else "non signé")
    messages.success(request, f"Bail supprimé.{suite}")
    return redirect(propriete)


def telecharger(request, pk, format_):
    return documents.document(request, get_object_or_404(Bail, pk=pk), MODELE, format_)


@require_POST
@verrouiller(Bail)
def deposer_signe(request, pk):
    objet = get_object_or_404(Bail, pk=pk)
    form = SignatureForm(request.POST, request.FILES, instance=Bail.objects.get(pk=pk))
    if form.is_valid():
        try:
            signer(request, objet, form)
        except ValidationError as erreur:
            messages.error(request, " ".join(erreur.messages))
            # Haut de page : le message s'y affiche, au-dessus de la préparation
            # qu'il réclame souvent ; l'ancre de l'étape le laissait hors de vue.
            return redirect(objet)
        messages.success(request, f"Signature du bail enregistrée au {objet.date_signature:%d/%m/%Y}.")
    else:
        for erreurs in form.errors.values():
            for erreur in erreurs:
                messages.error(request, erreur)
        return redirect(objet)
    return redirect(vers_etapes(objet))


def fichier_signe(request, pk):
    return documents.fichier_signe(request, get_object_or_404(Bail, pk=pk))


def _date_postee(request, nom):
    """Date saisie au format du navigateur (AAAA-MM-JJ), aujourd'hui par défaut,
    jamais dans le futur."""
    from datetime import date

    try:
        valeur = date.fromisoformat(request.POST.get(nom, ""))
    except ValueError:
        return timezone.localdate()
    return min(valeur, timezone.localdate())


def _indice_poste(request, nom):
    from decimal import Decimal, InvalidOperation

    texte = request.POST.get(nom, "").strip().replace(",", ".")
    try:
        valeur = Decimal(texte)
    except InvalidOperation:
        return None
    return valeur if 50 < valeur < 1000 else None


@require_POST
@verrouiller(Bail)
def reviser(request, pk):
    """Révision annuelle du loyer : appliquée au loyer calculé avec l'IRL, ou
    refusée par le propriétaire pour cette année. Le bail signé ne change pas."""
    objet = get_object_or_404(Bail.objects.select_related("propriete"), pk=pk)
    revision = revision_due(objet)
    if not revision:
        messages.error(request, "Aucune révision de loyer n'est à faire sur ce bail pour le moment.")
        return redirect(objet)
    if request.POST.get("decision") == "renoncer":
        RevisionLoyer.objects.create(
            bail=objet, date_revision=revision.date, applicable_le=revision.applicable_le,
            statut=RevisionLoyer.Statut.RENONCEE, ancien_loyer=revision.ancien_loyer, nouveau_loyer=revision.ancien_loyer,
            irl_libelle=revision.libelle_irl, irl_ancien=revision.irl_ancien, irl_nouveau=revision.irl_nouveau,
            cree_par=request.user,
        )
        noter(request, "révision de loyer", objet, f"Non révisé au {revision.date:%d/%m/%Y}")
        messages.success(request, f"Révision du {revision.date:%d/%m/%Y} notée comme non faite : le loyer reste "
                                  f"à {euros(revision.ancien_loyer)}.")
        return redirect(f"{objet.get_absolute_url()}#revision")
    if revision.gelee:
        messages.error(request, "Logement classé F ou G : la loi interdit d'augmenter le loyer.")
        return redirect(f"{objet.get_absolute_url()}#revision")
    # Indice publié depuis la dernière mise à jour : saisi ici, il sert à tous les baux.
    for nom, annee in (("irl_nouveau", revision.annee), ("irl_ancien", (revision.annee or 0) - 1)):
        valeur = _indice_poste(request, nom)
        if valeur and revision.trimestre and getattr(revision, nom) is None:
            IndiceIRL.objects.get_or_create(trimestre=revision.trimestre, annee=annee, defaults={"valeur": valeur})
            setattr(revision, nom, valeur)
    if revision.nouveau_loyer is None:
        messages.error(request, "Indiquer l'indice publié par l'INSEE pour calculer le nouveau loyer.")
        return redirect(f"{objet.get_absolute_url()}#revision")
    RevisionLoyer.objects.create(
        bail=objet, date_revision=revision.date, applicable_le=revision.applicable_le,
        statut=RevisionLoyer.Statut.APPLIQUEE, ancien_loyer=revision.ancien_loyer, nouveau_loyer=revision.nouveau_loyer,
        irl_libelle=revision.libelle_irl, irl_ancien=revision.irl_ancien, irl_nouveau=revision.irl_nouveau,
        cree_par=request.user,
    )
    noter(request, "révision de loyer", objet,
          f"{euros(revision.ancien_loyer)} → {euros(revision.nouveau_loyer)} au {revision.applicable_le:%d/%m/%Y}")
    messages.success(request, f"Loyer révisé : {euros(revision.nouveau_loyer)} hors charges à partir du "
                              f"{revision.applicable_le:%d/%m/%Y}. Le courrier au locataire est prêt ci-dessous.")
    return redirect(f"{objet.get_absolute_url()}#revision")


@require_POST
@verrouiller(Bail)
def assurance(request, pk):
    """Attestation d'assurance habitation reçue du locataire."""
    objet = get_object_or_404(Bail, pk=pk)
    objet.attestation_assurance_le = _date_postee(request, "attestation_assurance_le")
    objet.save(update_fields=["attestation_assurance_le", "modifie_le"])
    messages.success(request, f"Attestation d'assurance notée au {objet.attestation_assurance_le:%d/%m/%Y} : "
                              "elle sera redemandée dans un an.")
    return redirect(f"{objet.get_absolute_url()}#assurance")
