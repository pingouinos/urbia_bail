from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from contrats.models import Bail
from proprietes.models import Propriete
from candidatures.models import Candidature
from comptes.roles import est_administrateur
from mandats.models import Mandat

from .forms import OrganisationForm, type_image
from .models import Organisation


def locations_en_cours():
    """Logements dont la mise en location demande une action, avec l'étape
    atteinte et la prochaine chose à faire : préavis à relouer, candidatures à
    étudier, bail à rédiger, bail à faire signer."""
    lignes = {}

    def ligne(propriete):
        return lignes.setdefault(propriete.pk, {"propriete": propriete, "a_etudier": 0, "retenue": None, "a_signer": None, "preavis": None})

    candidatures = Candidature.objects.filter(
        bail__isnull=True, statut__in=[Candidature.Statut.A_ETUDIER, Candidature.Statut.RETENUE],
    ).select_related("propriete").prefetch_related("candidats")
    for candidature in candidatures:
        if candidature.statut == Candidature.Statut.RETENUE:
            ligne(candidature.propriete)["retenue"] = candidature
        else:
            ligne(candidature.propriete)["a_etudier"] += 1
    baux = Bail.objects.filter(date_fin_effective__isnull=True).filter(
        Q(date_signature__isnull=True) | Q(conge_recu_le__isnull=False)
    ).select_related("propriete").prefetch_related("locataires")
    for bail in baux:
        ligne(bail.propriete)["preavis" if bail.conge_recu_le else "a_signer"] = bail
    for propriete in Propriete.objects.filter(mise_en_location_le__isnull=False, actif=True):
        ligne(propriete)

    for l in lignes.values():
        propriete = l["propriete"]
        if l["preavis"]:
            l["detail"] = f"Libre le {l['preavis'].date_depart_prevue:%d/%m/%Y}"
        else:
            l["detail"] = f"Disponible le {propriete.disponible_le:%d/%m/%Y}" if propriete.a_louer and propriete.disponible_le else ""
        if l["a_signer"]:
            l["etape"] = f"Bail à faire signer : {l['a_signer'].noms_locataires or 'locataire à renseigner'}"
            l["action"], l["lien"] = "Ouvrir le bail", l["a_signer"].get_absolute_url()
        elif l["retenue"]:
            l["etape"] = f"Candidat retenu : {l['retenue'].noms or 'candidature'}"
            l["action"], l["lien"] = "Rédiger le bail", f"{reverse('contrats:creer')}?candidature={l['retenue'].pk}"
        elif l["a_etudier"]:
            nombre = l["a_etudier"]
            l["etape"] = f"{nombre} candidature{'s' if nombre > 1 else ''} à étudier"
            l["action"], l["lien"] = "Étudier", f"{reverse('candidatures:liste')}?propriete={propriete.pk}&statut=a_etudier"
        elif propriete.a_louer:
            l["etape"] = "À louer, aucune candidature"
            l["action"], l["lien"] = "Enregistrer une candidature", f"{reverse('candidatures:creer')}?propriete={propriete.pk}"
        else:
            l["etape"] = "Départ annoncé"
            l["action"], l["lien"] = "Mettre en location", reverse("proprietes:mettre_en_location", args=[propriete.pk])
    return sorted(lignes.values(), key=lambda l: (l["propriete"].ville, l["propriete"].adresse))


def accueil(request):
    """Page d'accueil centrée sur les mises en location en cours, puis les
    derniers baux."""
    est_admin = est_administrateur(request.user)
    return render(request, "core/accueil.html", {
        "est_admin": est_admin,
        "agence_manquants": Organisation.courante().manquants if est_admin else [],
        "derniers_baux": Bail.objects.select_related("propriete").prefetch_related("locataires").order_by("-modifie_le")[:8],
        "locations": locations_en_cours(),
        "nb_proprietes": Propriete.objects.filter(actif=True).count(),
        "nb_baux": Bail.objects.filter(date_fin_effective__isnull=True).count(),
        "nb_candidatures": Candidature.objects.filter(statut=Candidature.Statut.A_ETUDIER).count(),
        "nb_mandats": Mandat.objects.filter(date_fin__isnull=True).count(),
    })


def agence(request):
    """Fiche de l'agence, tenue par les administrateurs : ses mentions figurent
    sur les mandats et les baux."""
    if not est_administrateur(request.user):
        raise PermissionDenied
    organisation = Organisation.courante()
    form = OrganisationForm(request.POST or None, request.FILES or None, instance=organisation)
    if form.is_valid():
        form.save()
        messages.success(request, "Fiche de l'agence enregistrée.")
        return redirect("agence")
    return render(request, "core/agence.html", {
        "form": form, "organisation": organisation, "balises": list(organisation.contexte_document()),
        "historique": organisation.history.select_related("history_user")[:10],
    })


def logo_agence(request):
    """Logo déposé par l'agence. Public : il figure sur le formulaire du candidat."""
    logo = Organisation.courante().logo
    if not logo:
        raise Http404
    try:
        fichier = logo.open("rb")
    except FileNotFoundError:
        raise Http404
    type_ = type_image(fichier.read(12)) or "application/octet-stream"
    fichier.seek(0)
    reponse = FileResponse(fichier, content_type=type_)
    reponse["X-Content-Type-Options"] = "nosniff"
    return reponse


def sante(request):
    """Sonde pour Docker et le Synology : répond 200 si la base est joignable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"statut": "base indisponible"}, status=503)
    return JsonResponse({"statut": "ok"})
