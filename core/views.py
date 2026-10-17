from axes.utils import reset
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Exists, OuterRef, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_otp.plugins.otp_totp.models import TOTPDevice

from contrats.models import Bail
from proprietes.models import Propriete, baux_en_place
from candidatures.models import Candidature
from comptes.roles import est_administrateur
from mandats.models import Mandat

from .fichiers import servir
from .forms import OrganisationForm, UtilisateurForm
from .journal import noter
from .models import Evenement, Organisation
from .processus import processus_location
from .taches import ECHEANCES, de, par_rubrique, taches


def locations_en_cours():
    """Logements dont la mise en location demande une action, avec l'étape
    atteinte et la prochaine chose à faire : préavis à relouer, candidatures à
    étudier, bail à rédiger, bail à faire signer."""
    lignes = {}

    def ligne(propriete):
        return lignes.setdefault(propriete.pk, {"propriete": propriete, "a_etudier": 0, "a_etudier_seule": None, "retenue": None, "a_signer": None, "preavis": None})

    # Le logement a déjà son locataire (bail signé, sans congé) : les
    # candidatures restées à l'étude ne relancent pas sa mise en location.
    loue = Bail.objects.filter(
        propriete=OuterRef("propriete"), date_signature__isnull=False,
        date_fin_effective__isnull=True, conge_recu_le__isnull=True,
    )
    candidatures = Candidature.objects.filter(
        bail__isnull=True, statut__in=[Candidature.Statut.A_ETUDIER, Candidature.Statut.RETENUE],
    ).exclude(Exists(loue)).select_related("propriete").prefetch_related("candidats")
    for candidature in candidatures:
        if candidature.statut == Candidature.Statut.RETENUE:
            ligne(candidature.propriete)["retenue"] = candidature
        else:
            l = ligne(candidature.propriete)
            l["a_etudier"] += 1
            l["a_etudier_seule"] = candidature if l["a_etudier"] == 1 else None
    baux = Bail.objects.filter(date_fin_effective__isnull=True).filter(
        Q(date_signature__isnull=True) | Q(conge_recu_le__isnull=False)
    ).select_related("propriete").prefetch_related("locataires")
    for bail in baux:
        ligne(bail.propriete)["preavis" if bail.conge_recu_le else "a_signer"] = bail
    for propriete in Propriete.objects.filter(mise_en_location_le__isnull=False, actif=True):
        ligne(propriete)
    # Mandat en cours de chaque logement suivi (le plus récent), pour la frise.
    mandats = {
        mandat.propriete_id: mandat
        for mandat in Mandat.objects.filter(date_fin__isnull=True, propriete__in=list(lignes)).order_by("numero")
    }

    for l in lignes.values():
        propriete = l["propriete"]
        l["processus"] = processus_location(
            propriete, mandats.get(propriete.pk), l["a_etudier"], l["retenue"], l["a_signer"],
        )
        disponible = propriete.disponible_le
        if l["preavis"]:
            l["detail"] = f"libre le {l['preavis'].date_depart_prevue:%d/%m/%Y}"
        elif propriete.a_louer and disponible:
            l["detail"] = f"disponible {'depuis le' if disponible <= timezone.localdate() else 'le'} {disponible:%d/%m/%Y}"
        else:
            l["detail"] = ""
        # L'action à faire, verbe en tête : elle sert de titre à la ligne.
        if l["a_signer"]:
            noms = l["a_signer"].noms_locataires
            l["etape"] = f"Faire signer le bail {de(noms)}" if noms else "Faire signer le bail"
            l["action"], l["lien"] = "Ouvrir le bail", l["a_signer"].get_absolute_url()
        elif l["retenue"] and l["retenue"].lien_a_envoyer:
            # Le candidat retenu complète d'abord son dossier par son lien :
            # le bail se rédige ensuite, prérempli.
            noms = l["retenue"].noms
            l["etape"] = f"Envoyer le lien à {noms}" if noms else "Envoyer le lien au candidat retenu"
            l["action"], l["lien"] = "Envoyer le lien", f"{l['retenue'].get_absolute_url()}#lien"
        elif l["retenue"]:
            noms = l["retenue"].noms
            l["etape"] = f"Rédiger le bail {de(noms)}" if noms else "Rédiger le bail"
            l["action"], l["lien"] = "Rédiger le bail", f"{reverse('contrats:creer')}?candidature={l['retenue'].pk}"
            if not l["retenue"].rempli_le:
                l["detail"] = "lien envoyé, dossier en attente du candidat"
        elif l["a_etudier_seule"]:
            # Une seule candidature : sa fiche, où on la retient ou non.
            seule = l["a_etudier_seule"]
            l["etape"] = f"Étudier la candidature {de(seule.noms)}" if seule.noms else "Étudier la candidature"
            l["action"], l["lien"] = "Étudier", seule.get_absolute_url()
        elif l["a_etudier"]:
            nombre = l["a_etudier"]
            l["etape"] = f"Étudier {nombre} candidatures"
            l["action"], l["lien"] = "Étudier", f"{reverse('candidatures:liste')}?propriete={propriete.pk}&statut=a_etudier"
        elif propriete.a_louer:
            l["etape"] = "Enregistrer une candidature"
            l["action"], l["lien"] = "Enregistrer une candidature", f"{reverse('candidatures:creer')}?propriete={propriete.pk}"
        else:
            l["etape"] = "Remettre en location"
            l["action"], l["lien"] = "Mettre en location", reverse("proprietes:mettre_en_location", args=[propriete.pk])
    return sorted(lignes.values(), key=lambda l: (l["propriete"].ville, l["propriete"].adresse))


def modeles_a_corriger():
    """Modèles en service qu'une règle légale rend à corriger."""
    from documents.generation import defauts_modele
    from documents.views import MODELES

    return [{"nom": nom, "libelle": libelle} for nom, (libelle, _) in MODELES.items() if defauts_modele(nom)]


def accueil(request):
    """Page d'accueil : les actions en attente en une liste, rangée par sujet,
    chacune avec un repère de l'étape où attend son dossier ; puis les
    derniers contrats."""
    est_admin = est_administrateur(request.user)
    locations = locations_en_cours()
    a_faire = taches(locations)
    mandats = Mandat.objects.filter(date_fin__isnull=True).select_related("propriete__bailleur").prefetch_related(
        "propriete__pieces", "propriete__bailleur__pieces",
    )
    # Le parc : biens gérés (fiche active), répartis en loués, en préavis
    # (congé reçu, comme le filtre de la liste des biens) et libres (sans bail
    # en place). Un bien sans aucun bail dans l'application est compté à part :
    # importé d'Excel, il est souvent loué par un bail plus ancien.
    geres = Propriete.objects.filter(actif=True)
    libres = geres.filter(~Exists(baux_en_place()))
    en_preavis = geres.filter(Exists(baux_en_place().filter(conge_recu_le__isnull=False)))
    nb_geres, nb_libres, nb_preavis = geres.count(), libres.count(), en_preavis.count()
    nb_loues = nb_geres - nb_libres - nb_preavis
    parc = {
        "geres": nb_geres, "loues": nb_loues, "preavis": nb_preavis, "libres": nb_libres,
        "sans_bail": libres.filter(baux__isnull=True).count(),
        "sans_mandat": geres.filter(~Exists(Mandat.objects.filter(propriete=OuterRef("pk"), date_fin__isnull=True))).count(),
        "prochain_depart": Bail.objects.filter(
            propriete__in=en_preavis, conge_recu_le__isnull=False, date_depart_prevue__gte=timezone.localdate(),
        ).order_by("date_depart_prevue").values_list("date_depart_prevue", flat=True).first(),
        "part_loues": round(100 * nb_loues / nb_geres) if nb_geres else 0,
    }
    # Barre d'occupation et sa légende : seules les situations présentes. Les
    # biens sans bail enregistré sont à part (gris) : souvent loués par un bail
    # pas encore saisi, ils ne sont pas forcément libres.
    nb_sans_bail = parc["sans_bail"]
    parc["barre"] = [
        {"code": code, "nombre": nombre, "libelle": libelle, "part": 100 * nombre / nb_geres}
        for code, nombre, libelle in (
            ("loue", nb_loues, f"loué{'s' if nb_loues > 1 else ''}"),
            ("preavis", nb_preavis, "préavis"),
            ("libre", nb_libres - nb_sans_bail, f"libre{'s' if nb_libres - nb_sans_bail > 1 else ''}"),
            ("sans-bail", nb_sans_bail, "sans bail"),
        )
        if nombre
    ]
    return render(request, "core/accueil.html", {
        "est_admin": est_admin,
        "agence_manquants": Organisation.courante().manquants if est_admin else [],
        "modeles_a_corriger": modeles_a_corriger() if est_admin else [],
        "derniers_baux": Bail.objects.select_related("propriete__bailleur").prefetch_related("locataires").order_by("-modifie_le")[:5],
        "locations": locations, "taches": par_rubrique(a_faire), "nb_taches": len(a_faire),
        "nb_urgentes": sum(tache.urgent for tache in a_faire),
        "nb_echeances": sum(tache.rubrique == ECHEANCES for tache in a_faire),
        "parc": parc,
        # Mandats en cours pas encore en gestion : signature ou dossier à finir.
        "nb_mandats_a_finaliser": sum(1 for mandat in mandats if not mandat.signe or mandat.pieces_manquantes),
    })


def _administrateur(request):
    if not est_administrateur(request.user):
        raise PermissionDenied


def comptes_agence():
    """Les comptes de l'agence courante : un administrateur ne voit ni ne
    touche ceux des autres agences."""
    return get_user_model().objects.filter(membre__organisation=Organisation.courante())


def administration(request):
    """Accueil de l'administration : l'agence, les utilisateurs, les modèles."""
    _administrateur(request)
    from documents.regles import registre, resume

    return render(request, "core/administration.html", {
        "regles": resume(registre()),
        "organisation": Organisation.courante(),
        "nb_utilisateurs": comptes_agence().filter(is_active=True).count(),
    })


def utilisateurs(request):
    _administrateur(request)
    comptes = list(comptes_agence().order_by("-is_active", "last_name", "first_name", "username"))
    avec_mfa = set(TOTPDevice.objects.filter(confirmed=True).values_list("user_id", flat=True))
    for compte in comptes:
        compte.administrateur = est_administrateur(compte)
        compte.mfa = compte.pk in avec_mfa
        compte.google = not compte.has_usable_password()
    return render(request, "core/utilisateurs.html", {"comptes": comptes})


def utilisateur_editer(request, pk=None):
    _administrateur(request)
    compte = get_object_or_404(comptes_agence(), pk=pk) if pk else None
    form = UtilisateurForm(request.POST or None, instance=compte, acteur=request.user)
    if form.is_valid():
        creation = compte is None
        compte = form.save()
        noter(request, "compte créé" if creation else "compte modifié", compte.username,
              f"{form.cleaned_data['role']}, {'actif' if compte.is_active else 'désactivé'}"
              + (", mot de passe changé" if form.cleaned_data.get("mot_de_passe") else ""))
        messages.success(request, f"Compte de {compte.get_full_name() or compte.username} enregistré.")
        return redirect("utilisateurs")
    return render(request, "core/utilisateur_form.html", {
        "form": form, "compte": compte,
        "mfa": bool(compte) and TOTPDevice.objects.filter(user=compte, confirmed=True).exists(),
    })


@require_POST
def utilisateur_action(request, pk, action):
    """Dépannage d'un compte : nouvelle double authentification, ou
    déblocage après trop d'essais de mot de passe."""
    _administrateur(request)
    compte = get_object_or_404(comptes_agence(), pk=pk)
    nom = compte.get_full_name() or compte.username
    if action == "mfa":
        TOTPDevice.objects.filter(user=compte).delete()
        noter(request, "double authentification réinitialisée", compte.username)
        messages.success(request, f"Double authentification de {nom} réinitialisée : "
                                  "un nouveau code QR lui sera proposé à sa prochaine connexion.")
    else:
        reset(username=compte.username)
        noter(request, "compte débloqué", compte.username)
        messages.success(request, f"Compte de {nom} débloqué.")
    return redirect("utilisateur_modifier", pk=compte.pk)


def journal(request):
    """Journal de sécurité : connexions, documents ouverts, exports,
    suppressions, changements de droits. Gardé un an."""
    _administrateur(request)
    Evenement.tous.filter(date__lt=timezone.now() - timezone.timedelta(days=Evenement.CONSERVATION_JOURS)).delete()
    evenements = Evenement.objects.select_related("utilisateur")
    action, q = request.GET.get("action", ""), request.GET.get("q", "").strip()
    if action:
        evenements = evenements.filter(action=action)
    if q:
        evenements = evenements.filter(Q(identifiant__icontains=q) | Q(objet__icontains=q) | Q(ip__icontains=q))
    page = Paginator(evenements, 100).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "core/journal.html", {
        "page": page, "parametres": parametres.urlencode(),
        "actions": Evenement.objects.order_by("action").values_list("action", flat=True).distinct(),
    })


def agence(request):
    """Fiche de l'agence, tenue par les administrateurs : ses mentions figurent
    sur les mandats et les baux."""
    _administrateur(request)
    organisation = Organisation.courante()
    form = OrganisationForm(request.POST or None, request.FILES or None, instance=organisation)
    if form.is_valid():
        form.save()
        noter(request, "agence modifiée", organisation.designation, ", ".join(form.changed_data)[:300])
        messages.success(request, "Fiche de l'agence enregistrée.")
        return redirect("agence")
    return render(request, "core/agence.html", {
        "form": form, "organisation": organisation, "balises": list(organisation.contexte_document()),
        "historique": organisation.history.select_related("history_user")[:10],
    })


def logo_agence(request):
    """Logo déposé par l'agence. Public : il figure sur le formulaire du candidat."""
    reponse = servir(Organisation.courante().logo)
    # Affiché sur chaque page : le navigateur le garde une heure.
    reponse["Cache-Control"] = "private, max-age=3600"
    return reponse


def sante(request):
    """Sonde pour Docker et le Synology : répond 200 si la base est joignable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"statut": "base indisponible"}, status=503)
    return JsonResponse({"statut": "ok"})
