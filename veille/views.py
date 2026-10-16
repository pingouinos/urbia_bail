from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from core.journal import noter
from core.views import _administrateur

from .catalogue import SOURCES
from .forms import ExamenForm
from .models import Alerte, Decision, Source


def tableau(request):
    _administrateur(request)
    etats = {source.cle: source for source in Source.objects.all()}
    sources = [{"cle": cle, "reference": reference, "etat": etats.get(cle)} for cle, reference in SOURCES.items()]
    alertes = Alerte.objects.select_related("apres__source")
    statut = request.GET.get("statut", "")
    if statut in Alerte.Statut.values:
        alertes = alertes.filter(statut=statut)
    page = Paginator(alertes, 30).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "veille/tableau.html", {
        "sources": sources, "page": page, "statuts": Alerte.Statut.choices,
        "parametres": parametres.urlencode(),
        "a_analyser": Alerte.objects.filter(statut=Alerte.Statut.A_ANALYSER).count(),
    })


def detail(request, pk):
    _administrateur(request)
    alerte = get_object_or_404(Alerte.objects.select_related("avant__source", "apres__source"), pk=pk)
    form = ExamenForm(request.POST if request.method == "POST" else None, initial={"statut": alerte.statut})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            alerte = Alerte.objects.select_for_update().get(pk=pk)
            Decision.objects.create(
                alerte=alerte, auteur=request.user,
                auteur_nom=(request.user.get_full_name() or request.user.username)[:150],
                **form.cleaned_data,
            )
            alerte.statut = form.cleaned_data["statut"]
            alerte.save(update_fields=["statut"])
            noter(request, "alerte de veille examinée", f"Veille {alerte.pk}", alerte.get_statut_display())
        messages.success(request, "Examen enregistré. Aucun modèle ni contrat n'a été modifié.")
        return redirect("veille:detail", pk=pk)
    return render(request, "veille/detail.html", {
        "alerte": alerte, "reference": SOURCES.get(alerte.apres.source.cle),
        "form": form, "decisions": alerte.decisions.select_related("auteur"),
    })
