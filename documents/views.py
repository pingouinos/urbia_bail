"""Modèles Word des documents, et leur remplacement par un administrateur :
reprise depuis un Google Doc où Elodie retouche le texte, ou dépôt d'un
fichier Word. Un modèle n'est mis en service qu'après vérification de ses
balises et un remplissage d'essai ; la version remplacée reste disponible."""

import hashlib
import io
import mimetypes
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from docxtpl import DocxTemplate
from jinja2 import TemplateSyntaxError

from comptes.roles import est_administrateur
from core.fichiers import controler_archive, servir
from core.journal import noter
from proprietes.zones_tendues import derniere_liste

from . import google
from .generation import (
    CLAUSES_PERIMEES, DOCX, MODELES_PAR_DEFAUT, ConversionImpossible, chemin_modele, corriger_modele,
    defauts_modele, convertir_pdf, generer_docx, remplir,
)
from .archives import interdictions
from .models import Modele
from . import regles as registre_regles

# Modèles remplaçables : libellé, et objet dont le dernier modifié sert au
# remplissage d'essai.
MODELES = {
    "mandat_gestion.docx": ("Mandat de gestion", "mandats.Mandat"),
    "bail_habitation.docx": ("Bail d'habitation (nu ou meublé)", "contrats.Bail"),
}
TAILLE_MAX = 10 * 1024 * 1024


def document(request, objet, modele, format_):
    """Réponse de téléchargement du document d'un objet (mandat, bail) en
    Word ou en PDF ; sans PDF disponible, retour à la fiche avec un message."""
    if hasattr(objet, "versions_document"):
        from .archives import derniere_version
        from .archives_views import reponse_version
        version = derniere_version(objet)
        if version and (objet.signe or version.nature == "preparation"):
            return reponse_version(request, version, format_)
        if objet.signe:
            messages.error(request, "Contrat historique sans archive : utilisez l’exemplaire signé ou demandez une reprise historique à un administrateur.")
            return redirect(objet)
        obstacles = interdictions(objet)
        if obstacles:
            for obstacle in obstacles:
                messages.error(request, obstacle)
            return redirect(objet)
    contenu = generer_docx(modele, objet.contexte_document())
    type_, extension = DOCX, "docx"
    if format_ == "pdf":
        try:
            contenu = convertir_pdf(contenu, modele)
        except ConversionImpossible as erreur:
            messages.error(request, f"La conversion en PDF est indisponible ({erreur}) ; "
                                    "téléchargez la version Word.")
            return redirect(objet)
        type_, extension = "application/pdf", "pdf"
    reponse = HttpResponse(contenu, content_type=type_)
    reponse["Content-Disposition"] = f'attachment; filename="{objet.nom_fichier}.{extension}"'
    return reponse


def fichier_signe(request, objet):
    noter(request, "document ouvert", f"Exemplaire signé · {objet}")
    return servir(objet.fichier_signe)


def _verifier(request, nom):
    if not est_administrateur(request.user):
        raise PermissionDenied
    if nom not in MODELES:
        raise Http404


def _depose(nom):
    return Path(settings.MEDIA_ROOT) / "modeles" / nom


def _precedent(nom):
    return Path(settings.MEDIA_ROOT) / "modeles" / "precedent" / nom


def _variables(source):
    return DocxTemplate(source).get_undeclared_template_variables()


def _objet_essai(nom):
    return apps.get_model(MODELES[nom][1]).objects.order_by("-modifie_le").first()


def verifier_modele(nom, contenu):
    """Raison pour laquelle le modèle ne peut pas servir, ou None."""
    try:
        controler_archive(contenu)
    except ValidationError as erreur:
        return erreur.messages[0]
    try:
        variables = _variables(io.BytesIO(contenu))
    except TemplateSyntaxError as erreur:
        return f"Une balise est mal écrite ({erreur.message}) : vérifier les {{{{ … }}}} et {{% … %}}."
    except Exception:
        return "Ce fichier Word n'a pas pu être lu."
    inconnues = variables - _variables(MODELES_PAR_DEFAUT / nom)
    if inconnues:
        return "Balises inconnues : " + ", ".join(sorted(inconnues)) + "."
    perimees = defauts_modele(nom, contenu)
    if perimees:
        return perimees[0]
    objet = _objet_essai(nom)
    if objet is not None:
        # Remplissage d'essai avec le dernier document de ce type.
        try:
            remplir(DocxTemplate(io.BytesIO(contenu)), objet.contexte_document())
        except Exception as erreur:
            return f"Le remplissage d'essai échoue ({erreur})."
    return None


def mettre_en_service(nom, contenu, origine, utilisateur):
    """Remplace le modèle en service ; l'ancien devient la version
    précédente. Sans contenu, revient au modèle d'origine."""
    depose, precedent = _depose(nom), _precedent(nom)
    precedent.parent.mkdir(parents=True, exist_ok=True)
    ancien = chemin_modele(nom).read_bytes()
    if contenu is None:
        depose.unlink(missing_ok=True)
    else:
        depose.write_bytes(contenu)
    precedent.write_bytes(ancien)
    Modele.objects.update_or_create(nom=nom, defaults={
        "origine": origine, "mis_en_service_le": timezone.now(), "mis_en_service_par": utilisateur,
    })


def modeles(request):
    if not est_administrateur(request.user):
        raise PermissionDenied
    etats = {modele.nom: modele for modele in Modele.objects.select_related("mis_en_service_par")}
    liste = [
        {"nom": nom, "libelle": libelle, "etat": etats.get(nom) or Modele(nom=nom),
         "personnalise": _depose(nom).exists(), "precedent": _precedent(nom).exists(),
         "essai": _objet_essai(nom) is not None, "variables": sorted(_variables(MODELES_PAR_DEFAUT / nom))}
        for nom, (libelle, _) in MODELES.items()
    ]
    aujourdhui = timezone.localdate()
    for modele in liste:
        # À relire : jamais relu, relu il y a plus d'un an, ou remplacé depuis.
        etat, relu_le = modele["etat"], modele["etat"].relu_le
        modele["a_relire"] = (
            relu_le is None or aujourdhui - relu_le > registre_regles.VALIDITE
            or (etat.mis_en_service_le is not None and timezone.localdate(etat.mis_en_service_le) > relu_le)
        )
    return render(request, "documents/modeles.html", {"modeles": liste})


def regles(request):
    if not est_administrateur(request.user):
        raise PermissionDenied
    groupes = registre_regles.registre(timezone.localdate())
    liste = derniere_liste()
    return render(request, "documents/regles.html", {
        "groupes": groupes, "resume": registre_regles.resume(groupes),
        "changements_zone_tendue": liste.changements if liste else [],
        "relecture_avocat": registre_regles.RELECTURE_AVOCAT,
    })


@require_POST
def relu(request, nom):
    """Un administrateur note qu'il a relu le modèle en service, et d'après
    quels textes."""
    _verifier(request, nom)
    reference = " ".join(request.POST.get("reference", "").split())[:200]
    if not reference:
        messages.error(request, "Indiquer d'après quels textes le modèle a été relu.")
        return redirect("documents:modeles")
    Modele.objects.update_or_create(nom=nom, defaults={
        "relu_le": timezone.localdate(), "relu_par": request.user, "relu_reference": reference,
    })
    noter(request, "modèle relu", MODELES[nom][0], reference)
    messages.success(request, f"{MODELES[nom][0]} : relecture enregistrée.")
    return redirect("documents:modeles")


def _correction(nom):
    """(contenu corrigé, changements, empreinte du modèle en service) : la
    correction vue en essai reste celle qu'on met en service tant que le
    modèle n'a pas changé entre-temps."""
    source = chemin_modele(nom).read_bytes()
    contenu, changements = corriger_modele(nom, source)
    return contenu, changements, hashlib.sha256(source).hexdigest()


def _cle_essai(nom):
    return f"essai_correction:{nom}"


def corriger(request, nom):
    """Correction proposée d'un modèle aux clauses périmées : avant, après,
    document d'essai, puis mise en service par l'administrateur."""
    _verifier(request, nom)
    _, changements, empreinte = _correction(nom)
    if not changements:
        messages.info(request, f"Aucune correction à proposer pour « {MODELES[nom][0]} ».")
        return redirect("documents:regles")
    lignes = []
    for extrait, remplacement, avant, apres in changements:
        debut = avant.find(extrait)
        lignes.append({
            # Sans le renvoi vers « Modèles des documents » : on y est déjà.
            "message": CLAUSES_PERIMEES[nom][extrait].split(" Corriger le modèle")[0], "avant": avant[:debut], "retire": extrait,
            "ajoute": remplacement, "apres": avant[debut + len(extrait):],
        })
    return render(request, "documents/corriger.html", {
        "nom": nom, "libelle": MODELES[nom][0], "lignes": lignes,
        "essai_vu": request.session.get(_cle_essai(nom)) == empreinte,
        "avec_donnees": _objet_essai(nom) is not None, "lien_google": Modele.objects.filter(nom=nom, lien_google__gt="").exists(),
    })


def essai_correction(request, nom):
    """Document d'essai produit avec le modèle corrigé (le dernier document
    de ce type, ou à défaut le modèle corrigé lui-même) ; l'ouvrir débloque
    la mise en service de cette correction-là."""
    _verifier(request, nom)
    contenu, changements, empreinte = _correction(nom)
    if not changements:
        return redirect("documents:regles")
    request.session[_cle_essai(nom)] = empreinte
    objet = _objet_essai(nom)
    if objet is None:
        return HttpResponse(contenu, content_type=DOCX, headers={
            "Content-Disposition": f'attachment; filename="corrige-{nom}"'})
    rempli = generer_docx(nom, objet.contexte_document(), source=contenu)
    try:
        return HttpResponse(convertir_pdf(rempli, nom), content_type="application/pdf", headers={
            "Content-Disposition": f'inline; filename="essai-{nom[:-5]}.pdf"'})
    except ConversionImpossible:
        return HttpResponse(rempli, content_type=DOCX, headers={
            "Content-Disposition": f'attachment; filename="essai-{nom}"'})


@require_POST
def appliquer_correction(request, nom):
    _verifier(request, nom)
    contenu, changements, empreinte = _correction(nom)
    libelle = MODELES[nom][0]
    if not changements:
        return redirect("documents:regles")
    if request.session.get(_cle_essai(nom)) != empreinte:
        messages.error(request, "Ouvrir d'abord le document d'essai de la correction.")
        return redirect("documents:corriger", nom=nom)
    raison = verifier_modele(nom, contenu)
    if raison:
        messages.error(request, f"« {libelle} » n'a pas été corrigé. {raison}")
        return redirect("documents:corriger", nom=nom)
    mettre_en_service(nom, contenu, Modele.Origine.DEPOT, request.user)
    reference = "; ".join(f"« {remplacement} » au lieu de « {extrait} »" for extrait, remplacement, _, _ in changements)
    Modele.objects.filter(nom=nom).update(
        relu_le=timezone.localdate(), relu_par=request.user, relu_reference=f"la correction proposée ({reference})"[:200])
    del request.session[_cle_essai(nom)]
    noter(request, "modèle corrigé", nom, reference)
    messages.success(request, f"« {libelle} » corrigé et mis en service ; la version précédente reste récupérable. "
                              "Reportez la correction dans le Google Doc de l'agence.")
    return redirect("documents:regles")


def telecharger(request, nom):
    _verifier(request, nom)
    return FileResponse(chemin_modele(nom).open("rb"), as_attachment=True, filename=nom, content_type=DOCX)


def essai(request, nom):
    """Dernier document de ce type, produit avec le modèle en service."""
    _verifier(request, nom)
    objet = _objet_essai(nom)
    if objet is None:
        messages.error(request, f"Aucun {MODELES[nom][0].lower()} enregistré pour faire un essai.")
        return redirect("documents:modeles")
    return document(request, objet, nom, "pdf")


@require_POST
def reprendre(request, nom):
    _verifier(request, nom)
    libelle = MODELES[nom][0]
    modele, _ = Modele.objects.get_or_create(nom=nom)
    lien = request.POST.get("lien_google", "").strip()
    if lien != modele.lien_google:
        if google.identifiant(lien) is None:
            messages.error(request, "Coller le lien du Google Doc, de la forme https://docs.google.com/document/d/….")
            return redirect("documents:modeles")
        modele.lien_google = lien
        modele.save(update_fields=["lien_google"])
    try:
        contenu = google.telecharger(modele.lien_google)
    except google.LectureImpossible as erreur:
        messages.error(request, f"« {libelle} » n'a pas été repris. {erreur}")
        return redirect("documents:modeles")
    raison = verifier_modele(nom, contenu)
    if raison:
        messages.error(request, f"« {libelle} » n'a pas été mis en service. {raison}")
        return redirect("documents:modeles")
    mettre_en_service(nom, contenu, Modele.Origine.GOOGLE, request.user)
    noter(request, "modèle remplacé", nom, "repris de Google Docs")
    messages.success(request, f"« {libelle} » repris de Google Docs et mis en service. "
                              "Ouvrez le document d'essai pour vérifier la mise en page.")
    return redirect("documents:modeles")


@require_POST
def deposer(request, nom):
    _verifier(request, nom)
    fichier = request.FILES.get("fichier")
    if not fichier or not fichier.name.lower().endswith(".docx") or fichier.size > TAILLE_MAX:
        messages.error(request, "Fournir un fichier Word (.docx) de 10 Mo au maximum.")
        return redirect("documents:modeles")
    contenu = fichier.read()
    raison = verifier_modele(nom, contenu)
    if raison:
        messages.error(request, raison)
        return redirect("documents:modeles")
    mettre_en_service(nom, contenu, Modele.Origine.DEPOT, request.user)
    noter(request, "modèle remplacé", nom, "fichier déposé")
    messages.success(request, f"Nouveau modèle « {MODELES[nom][0]} » en service.")
    return redirect("documents:modeles")


@require_POST
def precedent(request, nom):
    _verifier(request, nom)
    if not _precedent(nom).exists():
        raise Http404
    mettre_en_service(nom, _precedent(nom).read_bytes(), Modele.Origine.PRECEDENT, request.user)
    noter(request, "modèle remplacé", nom, "version précédente")
    messages.success(request, f"Version précédente de « {MODELES[nom][0]} » remise en service.")
    return redirect("documents:modeles")


@require_POST
def retablir(request, nom):
    _verifier(request, nom)
    mettre_en_service(nom, None, Modele.Origine.ORIGINE, request.user)
    noter(request, "modèle remplacé", nom, "modèle d'origine")
    messages.success(request, f"Modèle d'origine « {MODELES[nom][0]} » rétabli.")
    return redirect("documents:modeles")
