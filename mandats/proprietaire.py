"""Fiche du propriétaire remplie en ligne, par le lien que l'agence lui envoie
depuis son mandat : ses coordonnées, le versement de ses loyers et les pièces
du dossier du mandant, en trois étapes. Chaque étape s'enregistre ; il peut
revenir avec le même lien jusqu'à son expiration."""

from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMessage
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from candidatures.forms import ReponseForm
from candidatures.views import envoi_configure
from core.agence import activer
from proprietes.models import Piece

from .forms import PieceProprietaireForm, ProprietaireVousForm, VersementForm
from .models import Mandat

ETAPES = ["Vous", "Versement", "Vos pièces"]


def adresse_lien(request, jeton):
    """Adresse ouverte sur Internet si URL_LOCATAIRES est renseignée, sinon
    celle du réseau local."""
    chemin = reverse("formulaire_proprietaire", args=[jeton])
    if settings.URL_LOCATAIRES:
        return settings.URL_LOCATAIRES + chemin
    return request.build_absolute_uri(chemin)


def lien(request, pk):
    """Lien personnel du propriétaire, et message pour le lui envoyer."""
    objet = get_object_or_404(Mandat.objects.select_related("propriete__bailleur"), pk=pk)
    if request.method == "POST" and request.POST.get("action") == "creer":
        if not objet.en_cours:
            messages.error(request, "Ce mandat est terminé : plus de lien à envoyer.")
            return redirect(objet)
        objet.creer_lien()
        return redirect("mandats:lien", pk=objet.pk)
    if not objet.lien_valide:
        messages.error(request, "Aucun lien valable pour ce mandat.")
        return redirect(objet)
    adresse = adresse_lien(request, objet.jeton)
    texte = objet.texte_lien(adresse, request.user.get_full_name())
    form = ReponseForm(request.POST or None, initial={"texte": texte})
    email = objet.propriete.bailleur.email
    destinataires = [email] if email else []
    sujet = f"Votre mandat de gestion pour le logement {objet.adresse_bien}"
    if request.method == "POST" and request.POST.get("action") == "envoyer" and form.is_valid():
        if envoi_configure() and destinataires:
            EmailMessage(subject=sujet, body=form.cleaned_data["texte"], to=destinataires).send()
            messages.success(request, "Lien envoyé au propriétaire.")
            return redirect(objet)
        messages.error(request, "Envoi impossible depuis l'application : utiliser votre messagerie.")
    return render(request, "mandats/lien.html", {
        "mandat": objet, "form": form, "adresse": adresse, "destinataires": destinataires,
        "envoi": envoi_configure() and bool(destinataires), "local": not settings.URL_LOCATAIRES,
        "mailto": "mailto:" + ",".join(destinataires) + "?"
        + urlencode({"subject": sujet, "body": texte}, quote_via=quote),
    })


# Pages ouvertes sans connexion, aux seuls détenteurs du lien.

@never_cache
def formulaire(request, jeton):
    objet = Mandat.par_jeton(jeton)
    if objet is None:
        return render(request, "mandats/proprietaire_invalide.html", status=404)
    # Le visiteur n'a pas d'agence : le lien lui ouvre celle du mandat, et elle seule.
    with activer(objet.agence):
        return _formulaire(request, objet)


def _formulaire(request, objet):
    etape = request.GET.get("etape", "1")
    etape = int(etape) if etape in ("1", "2", "3") else 1
    bailleur = objet.propriete.bailleur
    suite = f"{request.path}?etape={etape + 1}"
    contexte = {"mandat": objet, "bailleur": bailleur, "etape": etape, "etapes": ETAPES}
    donnees = request.POST if request.method == "POST" else None

    if etape == 1:
        form = ProprietaireVousForm(donnees, instance=bailleur)
        if donnees is not None and form.is_valid():
            with transaction.atomic():
                form.save()
                objet.reprendre_mandant()
            return redirect(suite)
        return render(request, "mandats/proprietaire.html", {**contexte, "form": form})

    if etape == 2:
        form = VersementForm(donnees, instance=bailleur)
        if donnees is not None and form.is_valid():
            form.save()
            return redirect(suite)
        return render(request, "mandats/proprietaire.html", {**contexte, "form": form})

    lignes = objet.dossier
    a_deposer = [ligne for ligne in lignes if ligne["piece"] is None or ligne["piece"].perime]
    erreur = None
    if donnees is not None and request.POST.get("action") == "envoyer":
        objet.rempli_le = timezone.now()
        objet.save(update_fields=["rempli_le", "modifie_le"])
        return redirect("formulaire_proprietaire_merci")
    if donnees is not None:
        form = PieceProprietaireForm(donnees, request.FILES, types=[ligne["type"] for ligne in a_deposer])
        if form.is_valid():
            ligne = next(ligne for ligne in a_deposer if ligne["type"] == form.cleaned_data["type"])
            cible = {"bailleur": ligne["cible"]} if ligne["du_bailleur"] else {"propriete": ligne["cible"]}
            Piece.objects.create(type=ligne["type"], fichier=form.cleaned_data["fichier"], **cible)
            return redirect(f"{request.path}?etape=3#pieces")
        erreur = (form.data.get("type"), " ".join(e for es in form.errors.values() for e in es))
    return render(request, "mandats/proprietaire.html", {
        **contexte, "lignes": lignes, "erreur": erreur,
        "deposees": sum(1 for ligne in lignes if ligne["piece"] and not ligne["piece"].perime),
    })


def merci(request):
    return render(request, "mandats/proprietaire_merci.html")
