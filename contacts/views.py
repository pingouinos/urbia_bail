"""Carnet des contacts : une fiche par personne ou entreprise."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect, render

from core.journal import noter
from proprietes.models import Bailleur, Propriete

from .forms import ContactForm
from .models import Contact, normaliser

CATEGORIES = [
    ("bailleur", "Bailleurs"), ("locataire", "Locataires"), ("candidat", "Candidats"),
    ("ancien", "Anciens locataires"), ("garant", "Garants"), ("representant", "Représentants"),
    ("associe", "Associés"),
]


def liste(request):
    categorie = request.GET.get("categorie")
    mots = normaliser(request.GET.get("q", "")).split()
    propriete = None
    if request.GET.get("propriete", "").isdigit():
        propriete = get_object_or_404(Propriete, pk=request.GET["propriete"])
    # Les catégories se déduisent des liens : le tri se fait une fois les
    # contacts chargés avec eux. Le carnet d'une agence reste de taille modeste.
    # Un candidat non retenu n'a plus de catégorie : il attend son effacement
    # et ne figure plus au carnet.
    contacts = [
        contact for contact in Contact.avec_liens()
        if (contact.codes or not contact.automatique)
        and (categorie not in dict(CATEGORIES) or categorie in contact.codes)
        and contact.correspond(mots)
        and (propriete is None or any(lien.propriete_id == propriete.pk for lien in contact.liens))
    ]
    if request.GET.get("tri") == "proprietes":
        contacts.sort(key=lambda contact: -len(contact.proprietes))
    page = Paginator(contacts, 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "contacts/liste.html", {
        "page": page, "parametres": parametres.urlencode(), "categories": CATEGORIES, "propriete": propriete,
    })


def contact(request, pk):
    objet = get_object_or_404(Contact.avec_liens(), pk=pk)
    return render(request, "contacts/contact.html", {
        "contact": objet, "historique": objet.history.select_related("history_user")[:10],
    })


def contact_editer(request, pk=None):
    objet = get_object_or_404(Contact, pk=pk) if pk else None
    initial = {}
    if objet is None and request.GET.get("categorie") in Contact.Categorie.values:
        initial["categorie"] = request.GET["categorie"]
    form = ContactForm(request.POST or None, instance=objet, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            objet = form.save()
            if objet.categorie == Contact.Categorie.BAILLEUR and not objet.bailleurs.exists():
                # Un propriétaire a aussi sa fiche de bailleur, à laquelle se
                # rattachent ses propriétés et ses mandats.
                Bailleur.objects.create(
                    type=Bailleur.Type.PERSONNE_MORALE if objet.entreprise else Bailleur.Type.PERSONNE_PHYSIQUE,
                    civilite=objet.civilite, nom=objet.nom, prenom=objet.prenom, adresse=objet.adresse,
                    code_postal=objet.code_postal, ville=objet.ville, email=objet.email, telephone=objet.telephone,
                )
            else:
                # L'identité et les coordonnées à jour suivent sur la fiche du bailleur.
                for bailleur in objet.bailleurs.all():
                    for champ in ("civilite", "nom", "prenom", "email", "telephone", "adresse", "code_postal", "ville"):
                        setattr(bailleur, champ, getattr(objet, champ))
                    # La nature choisie ici vaut aussi pour la fiche du bailleur ; sans cela, son
                    # type (société ou personne) reprendrait le dessus à l'enregistrement.
                    if objet.entreprise and not bailleur.societe:
                        bailleur.type = Bailleur.Type.PERSONNE_MORALE
                    elif not objet.entreprise and bailleur.societe:
                        bailleur.type = Bailleur.Type.PERSONNE_PHYSIQUE
                        bailleur.representant_civilite = bailleur.representant_nom = ""
                        bailleur.representant_prenom = bailleur.representant_qualite = bailleur.siren = ""
                        bailleur.associes.all().delete()
                    bailleur.save()
                Contact.effacer_orphelins()
        messages.success(request, "Contact enregistré.")
        return redirect(objet)
    return render(request, "contacts/contact_form.html", {
        "form": form, "contact": objet, "doublon": getattr(form, "doublon", None),
    })


@require_POST
def supprimer(request, pk):
    """Supprime un contact que rien ne relie plus à une propriété, un bail ou
    une candidature. Les autres disparaissent avec ce qui les relie."""
    objet = get_object_or_404(Contact.avec_liens(), pk=pk)
    if objet.liens or objet.bailleurs.all() or objet.locataires.all() or objet.candidats.all() or objet.garants.all():
        messages.error(request, "Suppression refusée : ce contact est relié à une propriété, un bail, une candidature "
                                "ou une société. Il disparaîtra avec ce qui le relie.")
        return redirect(objet)
    nom = objet.designation
    objet.delete()
    noter(request, "suppression", f"Contact · {nom}")
    messages.success(request, f"Contact supprimé : {nom}.")
    return redirect("contacts:liste")
