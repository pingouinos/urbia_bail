"""Envoi en signature, relance et annulation depuis la page des étapes, et
retour de DocuSeal quand un signataire ouvre, signe ou refuse."""

import hmac
import json
import logging

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.journal import noter

from . import docuseal, services
from .models import DemandeSignature, Signataire

CONTRATS = {"bail": "contrats.Bail", "mandat": "mandats.Mandat"}
journal = logging.getLogger(__name__)


def _vers_signature(contrat):
    return redirect(f"{contrat.get_absolute_url()}#etape-{4 if services.est_bail(contrat) else 2}")


@require_POST
def envoyer(request, type_, pk):
    if type_ not in CONTRATS:
        raise Http404
    contrat = get_object_or_404(apps.get_model(CONTRATS[type_]), pk=pk)
    try:
        demande = services.envoyer(contrat, request.user)
    except services.SignatureImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        nombre = demande.signataires.count()
        messages.success(request, f"Envoyé en signature : les {nombre} signataires reçoivent chacun un e-mail, "
                                  "dans l'ordre de la liste.")
        noter(request, "envoi en signature", str(contrat), f"{nombre} signataires")
    return _vers_signature(contrat)


@require_POST
def relancer(request, pk):
    signataire = get_object_or_404(Signataire.objects.select_related("demande"), pk=pk)
    try:
        services.relancer(signataire)
    except services.SignatureImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, f"{signataire.nom} a reçu un nouvel e-mail avec son lien.")
    return _vers_signature(signataire.demande.contrat)


@require_POST
def annuler(request, pk):
    demande = get_object_or_404(DemandeSignature, pk=pk)
    try:
        services.annuler(demande)
    except services.SignatureImpossible as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(request, "Envoi annulé : les liens reçus ne permettent plus de signer.")
        noter(request, "signature annulée", str(demande.contrat))
    return _vers_signature(demande.contrat)


@csrf_exempt
@require_POST
def retour_docuseal(request, jeton):
    """Webhook de DocuSeal. L'adresse porte un jeton secret, seul moyen pour
    DocuSeal de prouver que l'appel vient de lui. On ne croit rien du contenu :
    il sert seulement à savoir quelle demande relire dans DocuSeal."""
    attendu = settings.DOCUSEAL_JETON_WEBHOOK
    if not attendu or not hmac.compare_digest(jeton.encode(), attendu.encode()):
        raise Http404
    try:
        evenement = json.loads(request.body)
        donnees = evenement.get("data") or {}
        soumission = (donnees.get("submission") or {}).get("id") or donnees.get("submission_id")
        if not soumission and str(evenement.get("event_type", "")).startswith("submission."):
            soumission = donnees.get("id")
        soumission = int(soumission)
    except (ValueError, TypeError, AttributeError):
        return HttpResponse(status=400)
    demande = DemandeSignature.objects.filter(soumission=soumission).first()
    if demande is not None:
        try:
            services.synchroniser(demande)
        except docuseal.DocusealIndisponible:
            journal.exception("Demande %s non synchronisée", soumission)
            # DocuSeal réessaiera ; la commande synchroniser_signatures rattrape sinon.
            return HttpResponse(status=503)
    return HttpResponse(status=200)
