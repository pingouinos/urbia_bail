import datetime as dt

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from proprietes.models import Propriete
from candidatures.models import Candidature
from comptes.roles import est_administrateur
from contacts.models import Contact
from core.journal import noter
from mandats.models import Mandat
from documents import views as documents

from django.utils import timezone

from .forms import BailForm, FichierSigneForm, FinBailForm, LocataireFormSet, LogementForm, ProprietaireForm
from .models import Bail, Echeance, ajouter_mois

MODELE = "bail_habitation.docx"
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
            | Q(propriete__ref_ics__icontains=q)
            | Q(locataires__nom__icontains=q) | Q(locataires__prenom__icontains=q)
        ).distinct()
        filtre = (
            Q(mandant__icontains=q) | Q(propriete__adresse__icontains=q) | Q(propriete__ville__icontains=q)
            | Q(propriete__ref_ics__icontains=q)
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


def bail(request, pk):
    objet = get_object_or_404(Bail.objects.select_related("propriete__bailleur", "mandat"), pk=pk)
    return render(request, "contrats/bail.html", {
        "bail": objet,
        # Bannière de fin de parcours, une seule fois après la création.
        "vient_d_etre_cree": request.session.pop("bail_cree", None) == objet.pk,
        "historique": objet.history.select_related("history_user")[:10],
        "form_signe": FichierSigneForm(instance=objet),
        "echeances": _echeances(objet),
        "mois_propose": max(objet.date_effet, timezone.localdate()).strftime("%Y-%m"),
        "aujourd_hui": timezone.localdate().isoformat(),
    })


def _echeances(bail):
    """Les douze derniers mois du suivi des loyers, échéancier mis à jour."""
    if not bail.suivi_loyers_depuis:
        return []
    bail.completer_echeancier()
    return list(bail.echeances.all()[:12])


@require_POST
def loyers_suivre(request, pk):
    """Démarre (ou arrête) le suivi des loyers d'un bail. On choisit le
    premier mois suivi : un bail ancien ne fait pas naître des années
    d'échéances à pointer."""
    objet = get_object_or_404(Bail, pk=pk)
    if request.POST.get("action") == "arreter":
        if objet.echeances.filter(paye_le__isnull=False).exists():
            messages.error(request, "Des loyers sont déjà pointés sur ce bail : le suivi ne s'arrête plus.")
        else:
            objet.echeances.all().delete()
            objet.suivi_loyers_depuis = None
            objet.save(update_fields=["suivi_loyers_depuis", "modifie_le"])
            messages.success(request, "Suivi des loyers arrêté pour ce bail.")
        return redirect(f"{objet.get_absolute_url()}#loyers")
    try:
        depuis = dt.datetime.strptime(request.POST.get("depuis", ""), "%Y-%m").date()
    except ValueError:
        messages.error(request, "Indiquer le premier mois à suivre.")
        return redirect(f"{objet.get_absolute_url()}#loyers")
    if depuis < objet.date_effet.replace(day=1):
        messages.error(request, "Le suivi ne peut pas commencer avant la prise d'effet du bail.")
        return redirect(f"{objet.get_absolute_url()}#loyers")
    objet.suivi_loyers_depuis = depuis
    objet.save(update_fields=["suivi_loyers_depuis", "modifie_le"])
    nombre = len(objet.completer_echeancier())
    messages.success(request, f"Suivi des loyers démarré : {nombre} échéance{'s' if nombre > 1 else ''} créée{'s' if nombre > 1 else ''}.")
    return redirect(f"{objet.get_absolute_url()}#loyers")


@require_POST
def echeance_pointer(request, pk):
    """Pointe le paiement d'un loyer (ou l'annule, en cas d'erreur)."""
    objet = get_object_or_404(Echeance.objects.select_related("bail"), pk=pk)
    if request.POST.get("action") == "annuler":
        objet.paye_le = None
        objet.save(update_fields=["paye_le", "modifie_le"])
        messages.success(request, f"Paiement de {objet.libelle} annulé.")
        return redirect(objet)
    try:
        paye_le = dt.date.fromisoformat(request.POST.get("paye_le", ""))
    except ValueError:
        messages.error(request, "Indiquer la date du paiement.")
        return redirect(objet)
    if paye_le > timezone.localdate():
        messages.error(request, "La date du paiement ne peut pas être dans le futur.")
        return redirect(objet)
    objet.paye_le = paye_le
    objet.save(update_fields=["paye_le", "modifie_le"])
    messages.success(request, f"Loyer de {objet.libelle} pointé : la quittance est disponible.")
    return redirect(objet)


def echeance_document(request, pk, type_):
    """Avis d'échéance tant que le loyer n'est pas payé, quittance ensuite."""
    objet = get_object_or_404(Echeance.objects.select_related("bail__propriete__bailleur"), pk=pk)
    if type_ == "quittance" and not objet.payee:
        messages.error(request, "La quittance n'est délivrée qu'une fois le loyer payé.")
        return redirect(objet)
    modele = "quittance.docx" if type_ == "quittance" else "avis_echeance.docx"
    return documents.document(request, DocumentLoyer(objet, type_), modele, request.GET.get("format", "pdf"))


class DocumentLoyer:
    """Une échéance vue comme l'un de ses deux documents."""

    def __init__(self, echeance, type_):
        self.echeance, self.type = echeance, type_

    def contexte_document(self):
        return self.echeance.contexte_document()

    def get_absolute_url(self):
        return self.echeance.get_absolute_url()

    @property
    def nom_fichier(self):
        noms = " ".join(locataire.nom.upper() for locataire in self.echeance.bail.locataires.all())
        titre = "Quittance" if self.type == "quittance" else "Avis d'échéance"
        return f"{titre} {self.echeance.libelle} {noms}".strip()[:120]


def choisir(request):
    """« Nouveau contrat » : un mandat avec le bailleur, ou un bail avec le locataire."""
    return render(request, "contrats/choisir.html")


def bail_editer(request, pk=None):
    objet = get_object_or_404(Bail, pk=pk) if pk else None
    # Bail rédigé depuis une candidature retenue : logement, date d'entrée et
    # locataires viennent de la fiche du candidat.
    candidature = None
    pk_candidature = request.GET.get("candidature") or request.POST.get("candidature") or ""
    if objet is None and pk_candidature.isdigit():
        candidature = get_object_or_404(
            Candidature.objects.select_related("propriete"), statut=Candidature.Statut.RETENUE, bail__isnull=True,
            pk=pk_candidature,
        )
    if objet is None and request.method == "GET":
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
        form = BailForm(request.POST or None, instance=objet)
        locataires = LocataireFormSet(request.POST or None, instance=form.instance)
        if request.method == "POST" and form.is_valid() and locataires.is_valid():
            if candidature and form.cleaned_data["propriete"] != candidature.propriete:
                form.add_error("propriete", "La candidature porte sur un autre logement.")
            else:
                with transaction.atomic():
                    nouveau = objet is None
                    objet = form.save()
                    locataires.instance = objet
                    locataires.save()
                    if candidature:
                        candidature.bail = objet
                        candidature.save(update_fields=["bail", "modifie_le"])
                    if nouveau and objet.propriete.a_louer:
                        # Le bail rédigé clôt la mise en location.
                        objet.propriete.mise_en_location_le = objet.propriete.disponible_le = None
                        objet.propriete.save(update_fields=["mise_en_location_le", "disponible_le", "modifie_le"])
                if nouveau:
                    request.session["bail_cree"] = objet.pk
                else:
                    messages.success(request, "Bail enregistré.")
                return redirect(objet)
    return render(request, "contrats/bail_form.html", {
        "form": form, "locataires": locataires, "bail": objet, "candidature": candidature,
        "logement": form.instance.propriete if form.instance.propriete_id else None,
    })


def choisir_logement(request):
    """Étape 1 du parcours : le logement loué, retrouvé par son adresse, sa
    référence ou son propriétaire."""
    proprietes = Propriete.objects.filter(actif=True, usage=Propriete.Usage.HABITATION).select_related("bailleur")
    q = request.GET.get("q", "").strip()
    for mot in q.split():
        proprietes = proprietes.filter(
            Q(adresse__icontains=mot) | Q(complement__icontains=mot) | Q(code_postal__icontains=mot)
            | Q(ville__icontains=mot) | Q(ref_ics__icontains=mot) | Q(bailleur__nom__icontains=mot)
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


def fin(request, pk):
    """Départ du locataire en une page : le congé reçu et la fin du préavis
    sont proposés d'avance, puis le départ effectif une fois le préavis fini."""
    objet = get_object_or_404(Bail.objects.select_related("propriete"), pk=pk)
    aujourd_hui = timezone.localdate()
    initial = {}
    if not objet.conge_recu_le:
        initial = {
            "conge_origine": Bail.Origine.LOCATAIRE, "conge_recu_le": aujourd_hui,
            "date_depart_prevue": ajouter_mois(aujourd_hui, objet.mois_preavis),
        }
    elif not objet.date_fin_effective and objet.date_depart_prevue <= aujourd_hui:
        initial = {"date_fin_effective": objet.date_depart_prevue}
    form = FinBailForm(request.POST or None, instance=objet, initial=initial)
    if request.method == "POST" and form.is_valid():
        objet = form.save()
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
        return redirect(propriete)
    return render(request, "contrats/fin.html", {"bail": objet, "form": form})


@require_POST
def supprimer(request, pk):
    """Supprime un bail rédigé par erreur ou resté sans suite. Un bail signé
    engage les parties : il se termine par le départ du locataire, et seul un
    administrateur peut le supprimer."""
    objet = get_object_or_404(Bail.objects.select_related("propriete"), pk=pk)
    if objet.signe and not est_administrateur(request.user):
        messages.error(request, "Ce bail est signé : seul un administrateur peut le supprimer. "
                                "Pour y mettre fin, utilisez « Départ du locataire ».")
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
    return documents.fichier_signe(request, get_object_or_404(Bail, pk=pk))
