"""Versions contractuelles et transitions, appelées sous verrou du contrat.

La préparation est obligatoire avant une nouvelle signature. Une reprise
historique ne prouve jamais quel document a été envoyé aux parties.
"""
import hashlib
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from comptes.roles import acces_metier, est_administrateur
from core.journal import noter
from .generation import chemin_modele, defauts_modele, generer_docx
from .models import PdfArchive, VersionContrat


def verrouiller(model):
    """Sérialise les transitions pour un même contrat (PostgreSQL)."""
    def decorer(view):
        @wraps(view)
        def appel(request, pk, *args, **kwargs):
            if not acces_metier(request.user):
                raise PermissionDenied
            with transaction.atomic():
                # La vue relit ensuite le contrat dans cette même transaction.
                from django.shortcuts import get_object_or_404
                get_object_or_404(model.objects.select_for_update(), pk=pk)
                return view(request, pk, *args, **kwargs)
        return appel
    return decorer


def nom_modele(objet):
    return "bail_habitation.docx" if objet.code_type == "bail" else "mandat_gestion.docx"


def interdictions(objet):
    """Raisons légales d'empêcher la préparation et la signature du contrat :
    celles du contrat lui-même et les clauses périmées du modèle en service."""
    return list(getattr(objet, "interdictions", [])) + defauts_modele(nom_modele(objet))


def derniere_version(objet):
    return objet.versions_document.first()


def creer(objet, user, nature, motif, source=None, scan=None):
    nom = nom_modele(objet)
    contexte = source.contexte if source else objet.contexte_courant()
    modele = bytes(source.modele) if source else chemin_modele(nom).read_bytes()
    docx = bytes(source.docx) if source else generer_docx(nom, contexte, source=modele)
    contenu_scan, nom_scan = None, ""
    if scan:
        with scan.open("rb") as fichier:
            contenu_scan = fichier.read()
        nom_scan = scan.name.rsplit("/", 1)[-1]
    version = VersionContrat.objects.create(
        **{objet.code_type: objet}, nature=nature, auteur=user, auteur_nom=user.get_username(), motif=motif,
        source=source, contexte=contexte, modele=modele, docx=docx,
        empreinte=hashlib.sha256(docx).hexdigest(), date_signature=objet.date_signature,
        scan=contenu_scan, scan_nom=nom_scan,
    )

    if source:
        pdf = PdfArchive.objects.filter(version=source).first()
        if pdf:
            PdfArchive.objects.create(version=version, contenu=pdf.contenu)
    return version


def assurer_historique(objet, user):
    if objet.signe and not derniere_version(objet):
        return creer(objet, user, "reprise",
                     "Reconstitution à la date de reprise ; données et modèle initiaux inconnus.",
                     scan=objet.fichier_signe)
    return derniere_version(objet)


def motif_correction(request):
    motif = request.POST.get("motif", "").strip()
    if not est_administrateur(request.user):
        raise PermissionDenied
    if not motif:
        raise ValidationError("Indiquer le motif de la correction.")
    return motif


@transaction.atomic
def signer(request, objet, form):
    """Un scan tardif reste possible ; il n'autorise pas à changer la date."""
    if objet.signe:
        if form.cleaned_data["date_signature"] != objet.date_signature:
            raise ValidationError("La date de signature est verrouillée. Retirez la signature si le parcours l'autorise.")
        if "fichier_signe" not in request.FILES:
            return
        assurer_historique(objet, request.user)
        source = derniere_version(objet)
        form.save()
        objet.refresh_from_db()
        creer(objet, request.user, "scan", "Dépôt de l'exemplaire signé ; archives précédentes conservées.",
              source=source, scan=objet.fichier_signe)
    else:
        if not objet.en_cours:
            raise ValidationError("Un contrat terminé ne peut plus être signé.")
        if interdictions(objet):
            raise ValidationError(interdictions(objet))
        source = derniere_version(objet)
        source = source if source and source.nature == "preparation" else None
        if source is None:
            raise ValidationError("Préparez la version à signer avant d’enregistrer la signature.")
        if source:
            # La date n'était pas connue au moment de préparer le document.
            contexte = objet.contexte_courant()
            contexte["date_signature"] = source.contexte.get("date_signature", "")
            if contexte != source.contexte:
                raise ValidationError("Les données ont changé : préparez une nouvelle version avant de signer.")
        form.save()
        objet.refresh_from_db()
        creer(objet, request.user, "signature",
              "Signature de la version préparée.",
              source=source, scan=objet.fichier_signe)
    noter(request, "signature", objet, "Date inchangée pour un dépôt tardif ; version archivée.")
