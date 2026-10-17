"""Espace Plateforme : les agences qui utilisent l'installation, vues par
l'exploitant (le superutilisateur). Il les crée, les désactive et suit leur
usage, sans jamais voir leurs biens, leurs baux ni leurs candidats."""

import secrets

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from comptes.roles import ADMINISTRATEURS, attribuer_role, est_administrateur

from .agence import activer
from .journal import noter
from .models import Organisation


def _exploitant(request):
    if not request.user.is_superuser:
        raise PermissionDenied


def agences_avec_usage():
    return Organisation.objects.annotate(
        nb_comptes=Count("membres", filter=Q(membres__utilisateur__is_active=True)),
        derniere_connexion=Max("membres__utilisateur__last_login"),
    )


def plateforme(request):
    _exploitant(request)
    agences = list(agences_avec_usage().order_by("-active", "nom", "pk"))
    return render(request, "core/plateforme.html", {
        "version": version_en_ligne(),
        "agences": agences,
        "nb_actives": sum(agence.active for agence in agences),
        "nb_comptes": sum(agence.nb_comptes for agence in agences if agence.active),
    })


def version_en_ligne():
    """Commit et date de l'image en service, posés par GitHub à la construction."""
    commit = settings.VERSION
    if commit == "dev":
        return {"court": "dev"}
    depot = settings.TICKETS_DEPOT_GITHUB
    return {
        "court": commit[:7],
        "lien": f"https://github.com/{depot}/commit/{commit}" if depot else "",
        "date": parse_datetime(settings.DATE_VERSION) if settings.DATE_VERSION else None,
    }


class NouvelleAgenceForm(forms.Form):
    nom = forms.CharField(label="Nom commercial", max_length=120)
    ville = forms.CharField(
        label="Ville", max_length=80, required=False,
        help_text="Le reste de la fiche (carte professionnelle, garantie…) est rempli par l'agence elle-même.",
    )
    prenom = forms.CharField(label="Prénom", max_length=150)
    nom_admin = forms.CharField(label="Nom", max_length=150)
    email = forms.EmailField(
        label="Adresse e-mail",
        help_text="Elle sert d'identifiant. La double authentification sera demandée à la première connexion.",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        comptes = get_user_model().objects
        if comptes.filter(email__iexact=email).exists() or comptes.filter(username__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cette adresse.")
        return email


def agence_nouvelle(request):
    """Une agence et son premier administrateur, qui créera ensuite ses
    collègues. Le mot de passe initial n'est montré qu'une fois."""
    _exploitant(request)
    form = NouvelleAgenceForm(request.POST or None)
    if form.is_valid():
        donnees = form.cleaned_data
        mot_de_passe = secrets.token_urlsafe(12)
        with transaction.atomic():
            agence = Organisation.objects.create(nom=donnees["nom"], ville=donnees["ville"])
            with activer(agence):
                compte = get_user_model().objects.create_user(
                    username=donnees["email"], email=donnees["email"], password=mot_de_passe,
                    first_name=donnees["prenom"], last_name=donnees["nom_admin"],
                )
            attribuer_role(compte, ADMINISTRATEURS)
        noter(request, "agence créée", agence.designation, f"administrateur {compte.username}")
        # Rendu sans redirection : le mot de passe ne passe ni par la session ni par un message.
        return render(request, "core/plateforme_agence_creee.html", {
            "agence": agence, "compte": compte, "mot_de_passe": mot_de_passe,
        })
    return render(request, "core/plateforme_agence_nouvelle.html", {"form": form})


def agence_fiche(request, pk):
    _exploitant(request)
    agence = get_object_or_404(agences_avec_usage(), pk=pk)
    comptes = list(
        get_user_model().objects.filter(membre__organisation=agence).order_by("-is_active", "last_name", "first_name")
    )
    for compte in comptes:
        compte.administrateur = est_administrateur(compte)
    return render(request, "core/plateforme_agence.html", {"agence": agence, "comptes": comptes})


@require_POST
def agence_etat(request, pk):
    """Désactive ou réactive une agence. Ses données restent intactes."""
    _exploitant(request)
    agence = get_object_or_404(Organisation, pk=pk)
    agence.active = request.POST.get("active") == "1"
    agence.save(update_fields=["active", "modifie_le"])
    noter(request, "agence réactivée" if agence.active else "agence désactivée", agence.designation)
    messages.success(request, f"{agence.designation} {'réactivée' if agence.active else 'désactivée'}.")
    return redirect("plateforme_agence", pk=agence.pk)
