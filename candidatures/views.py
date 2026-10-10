from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from biens.models import Bien

from .forms import (
    CandidatureForm, DossierLocataireForm, ReponseForm, candidat_formset, candidat_locataire_formset,
)
from .models import Candidature, Garant


def envoi_configure():
    """Faux tant que EMAIL_URL n'est pas renseigné : les messages ne
    partiraient pas."""
    return not settings.EMAIL_BACKEND.endswith(("console.EmailBackend", "dummy.EmailBackend"))


def liste(request):
    # Pas de tâche planifiée sur le NAS : la purge passe aussi à chaque
    # consultation de la liste.
    Candidature.purger()
    candidatures = Candidature.objects.select_related("bien", "bail").prefetch_related("candidats")
    q = request.GET.get("q", "").strip()
    if q:
        candidatures = candidatures.filter(
            Q(bien__adresse__icontains=q) | Q(bien__ville__icontains=q) | Q(bien__ref_ics__icontains=q)
            | Q(candidats__nom__icontains=q) | Q(candidats__prenom__icontains=q)
        ).distinct()
    bien = None
    if request.GET.get("bien", "").isdigit():
        bien = get_object_or_404(Bien, pk=request.GET["bien"])
        candidatures = candidatures.filter(bien=bien)
    if request.GET.get("statut") in Candidature.Statut.values:
        candidatures = candidatures.filter(statut=request.GET["statut"])
    page = Paginator(candidatures, 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "candidatures/liste.html", {
        "page": page, "parametres": parametres.urlencode(), "statuts": Candidature.Statut.choices, "bien": bien,
    })


def candidature(request, pk):
    objet = get_object_or_404(Candidature.objects.select_related("bien__bailleur", "decide_par", "bail"), pk=pk)
    return render(request, "candidatures/candidature.html", {"candidature": objet})


def candidature_editer(request, pk=None):
    objet = get_object_or_404(Candidature, pk=pk) if pk else None
    initial = {"bien": request.GET["bien"]} if objet is None and request.GET.get("bien") else {}
    form = CandidatureForm(request.POST or None, instance=objet, initial=initial)
    candidats = candidat_formset(request.POST or None, form.instance)
    if request.method == "POST" and form.is_valid() and candidats.is_valid():
        creation = objet is None
        with transaction.atomic():
            objet = form.save()
            candidats.instance = objet
            candidats.save()
        if creation:
            messages.success(request, "Candidature enregistrée. Envoyez le lien au candidat pour qu'il complète son dossier.")
        else:
            messages.success(request, "Candidature enregistrée.")
        return redirect(objet)
    return render(request, "candidatures/candidature_form.html", {
        "form": form, "candidats": candidats, "candidature": objet,
    })


@require_POST
def decider(request, pk):
    objet = get_object_or_404(Candidature, pk=pk)
    statut = request.POST.get("statut")
    if statut not in Candidature.Statut.values or objet.bail_id:
        messages.error(request, "Décision impossible.")
        return redirect(objet)
    objet.decider(statut, request.user)
    if statut == Candidature.Statut.NON_RETENUE and objet.email and not objet.refus_envoye_le:
        return redirect("candidatures:reponse", pk=objet.pk)
    messages.success(request, f"Candidature : {objet.get_statut_display().lower()}.")
    return redirect(objet)


def reponse(request, pk):
    objet = get_object_or_404(Candidature.objects.select_related("bien"), pk=pk)
    if objet.statut != Candidature.Statut.NON_RETENUE:
        messages.error(request, "La réponse ne s'envoie qu'à une candidature non retenue.")
        return redirect(objet)
    signataire = request.user.get_full_name()
    form = ReponseForm(request.POST or None, initial={"texte": objet.texte_refus(signataire)})
    destinataires = sorted({c.email for c in objet.candidats.all() if c.email})
    if request.method == "POST" and form.is_valid():
        if request.POST.get("action") == "envoyer":
            if not envoi_configure():
                messages.error(request, "L'envoi d'e-mails n'est pas configuré : copier le message dans Gmail.")
                return render(request, "candidatures/reponse.html", {
                    "candidature": objet, "form": form, "destinataires": destinataires, "envoi": False,
                })
            EmailMessage(
                subject=f"Votre candidature pour le logement {objet.bien.adresse}, {objet.bien.ville}",
                body=form.cleaned_data["texte"],
                to=destinataires,
            ).send()
            messages.success(request, "Réponse envoyée au candidat.")
        else:
            messages.success(request, "Réponse notée comme envoyée.")
        objet.refus_envoye_le = timezone.now()
        objet.save(update_fields=["refus_envoye_le", "modifie_le"])
        return redirect(objet)
    return render(request, "candidatures/reponse.html", {
        "candidature": objet, "form": form, "destinataires": destinataires, "envoi": envoi_configure(),
    })


@require_POST
def effacer(request, pk):
    objet = get_object_or_404(Candidature, pk=pk)
    if objet.bail_id:
        messages.error(request, "Cette candidature est devenue un bail : elle reste au dossier du locataire.")
        return redirect(objet)
    bien = objet.bien
    objet.delete()
    messages.success(request, "Candidature et données du candidat effacées.")
    return redirect(f"{reverse('candidatures:liste')}?bien={bien.pk}")


def adresse_lien(request, jeton):
    """Adresse complète du formulaire : celle ouverte sur Internet si
    URL_LOCATAIRES est renseignée, sinon celle du réseau local."""
    chemin = reverse("formulaire_locataire", args=[jeton])
    if settings.URL_LOCATAIRES:
        return settings.URL_LOCATAIRES + chemin
    return request.build_absolute_uri(chemin)


def lien(request, pk):
    """Lien personnel du candidat, et message pour le lui envoyer."""
    objet = get_object_or_404(Candidature.objects.select_related("bien"), pk=pk)
    if request.method == "POST" and request.POST.get("action") == "creer":
        if not objet.lien_possible:
            messages.error(request, "Le lien s'envoie à une candidature à l'étude ou retenue, avant la rédaction du bail.")
            return redirect(objet)
        objet.creer_lien()
        return redirect("candidatures:lien", pk=objet.pk)
    if not objet.lien_valide:
        messages.error(request, "Aucun lien valable pour cette candidature.")
        return redirect(objet)
    adresse = adresse_lien(request, objet.jeton)
    texte = objet.texte_lien(adresse, request.user.get_full_name())
    form = ReponseForm(request.POST or None, initial={"texte": texte})
    destinataires = sorted({c.email for c in objet.candidats.all() if c.email})
    sujet = (
        f"Votre {'bail' if objet.statut == Candidature.Statut.RETENUE else 'candidature'} "
        f"pour le logement {objet.bien.adresse}, {objet.bien.ville}"
    )
    if request.method == "POST" and request.POST.get("action") == "envoyer" and form.is_valid():
        if envoi_configure() and destinataires:
            EmailMessage(subject=sujet, body=form.cleaned_data["texte"], to=destinataires).send()
            messages.success(request, "Lien envoyé au candidat.")
            return redirect(objet)
        messages.error(request, "Envoi impossible depuis l'application : utiliser votre messagerie.")
    return render(request, "candidatures/lien.html", {
        "candidature": objet, "form": form, "adresse": adresse, "destinataires": destinataires,
        "envoi": envoi_configure() and bool(destinataires), "local": not settings.URL_LOCATAIRES,
        "mailto": "mailto:" + ",".join(destinataires) + "?"
        + urlencode({"subject": sujet, "body": texte}, quote_via=quote),
    })


# Pages ouvertes sans connexion, aux seuls détenteurs du lien.

@never_cache
def formulaire_locataire(request, jeton):
    objet = Candidature.par_jeton(jeton)
    if objet is None:
        return render(request, "candidatures/locataire_invalide.html", status=404)
    form = DossierLocataireForm(request.POST or None, instance=objet)
    candidats = candidat_locataire_formset(objet, request.POST or None)
    if request.method == "POST" and formulaire_locataire_valide(form, candidats):
        with transaction.atomic():
            objet = form.save(commit=False)
            if "lien_dossierfacile" in form.changed_data:
                # Nouveau dossier : les pièces sont à revoir.
                objet.dossier_verifie = False
            objet.jeton = None
            objet.rempli_le = timezone.now()
            objet.save()
            candidats.save()
            for sous_form in candidats.forms:
                # Ligne du second locataire laissée vide : rien à enregistrer.
                if sous_form.instance.pk:
                    sous_form.garants.save()
            if objet.garantie == Candidature.Garantie.AUCUNE and Garant.objects.filter(
                    candidat__candidature=objet).exists():
                objet.garantie = Candidature.Garantie.PERSONNE
                objet.save(update_fields=["garantie"])
        return redirect("formulaire_locataire_merci")
    return render(request, "candidatures/locataire.html", {
        "candidature": objet, "form": form, "candidats": candidats,
    })


def formulaire_locataire_valide(form, candidats):
    # Tous les formulaires sont validés, pour afficher toutes les erreurs.
    valide = all([form.is_valid(), candidats.is_valid(), *(f.garants.is_valid() for f in candidats.forms)])
    for sous_form in candidats.forms:
        if not sous_form.instance.pk and not sous_form.has_changed() and sous_form.garants.has_changed():
            sous_form.add_error(None, "Indiquez d'abord ce locataire, puis ses garants.")
            valide = False
    return valide


def formulaire_locataire_merci(request):
    return render(request, "candidatures/locataire_merci.html")
