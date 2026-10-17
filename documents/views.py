"""Modèles des documents (HTML) et leur modification par un administrateur
dans l'éditeur intégré. Une version n'est mise en service qu'après
vérification de ses données et un document d'essai ; la version remplacée
reste disponible."""

import hashlib
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from jinja2 import TemplateError

from comptes.roles import est_administrateur
from core.fichiers import servir
from core.journal import noter
from proprietes.zones_tendues import derniere_liste

from . import gabarits
from .generation import (
    CLAUSES_PERIMEES, HTML, MODELES_PAR_DEFAUT, ConversionImpossible, convertir_ancien, convertir_pdf,
    corriger_modele, defauts_modele, fichier, generer_html, lire_modele,
)
from .archives import interdictions
from .models import Modele
from . import regles as registre_regles

# Modèles remplaçables : libellé, et objet dont le dernier modifié sert au
# remplissage d'essai.
MODELES = {
    "mandat_gestion": ("Mandat de gestion", "mandats.Mandat"),
    "bail_habitation": ("Bail d'habitation (nu ou meublé)", "contrats.Bail"),
}
# Groupe du registre des règles légales, et textes proposés pour noter la
# relecture quand personne ne l'a encore fait.
DOCUMENT_REGLES = {"mandat_gestion": registre_regles.MANDAT, "bail_habitation": registre_regles.BAIL}
REFERENCE_RELECTURE = {
    "mandat_gestion": "la loi Hoguet et le code de la consommation",
    "bail_habitation": "la loi du 6 juillet 1989 et le décret 2015-587",
}
TAILLE_MAX = 2 * 1024 * 1024


def document(request, objet, modele, format_="pdf"):
    """Téléchargement du document d'un objet (mandat, bail) en PDF ; sans
    conversion possible, retour à la fiche avec un message."""
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
    try:
        contenu = convertir_pdf(generer_html(modele, objet.contexte_document()).encode("utf-8"), modele)
    except ConversionImpossible as erreur:
        messages.error(request, f"Le PDF n'a pas pu être produit ({erreur}) ; réessayez dans un instant.")
        return redirect(objet)
    reponse = HttpResponse(contenu, content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="{objet.nom_fichier}.pdf"'
    return reponse


def fichier_signe(request, objet):
    noter(request, "document ouvert", f"Exemplaire signé · {objet}")
    return servir(objet.fichier_signe)


def _verifier(request, nom):
    if not est_administrateur(request.user):
        raise PermissionDenied
    if nom not in MODELES:
        raise Http404
    for chemin in (_depose(nom), _precedent(nom), _essai(nom)):
        convertir_ancien(chemin)


def _depose(nom):
    return Path(settings.MEDIA_ROOT) / "modeles" / fichier(nom)


def _precedent(nom):
    return Path(settings.MEDIA_ROOT) / "modeles" / "precedent" / fichier(nom)


def _essai(nom):
    """Version modifiée, en attente de mise en service."""
    return Path(settings.MEDIA_ROOT) / "modeles" / "essai" / fichier(nom)


def _lire(chemin):
    return chemin.read_text(encoding="utf-8")


def _origine_essai(nom):
    return _essai(nom).with_suffix(".origine")


def _cle_version(nom):
    return f"essai_version:{nom}"


def _empreinte(contenu):
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


def preparer_essai(nom, contenu, origine):
    essai = _essai(nom)
    essai.parent.mkdir(parents=True, exist_ok=True)
    essai.write_text(contenu, encoding="utf-8")
    _origine_essai(nom).write_text(origine)


def abandonner_essai(nom):
    _essai(nom).unlink(missing_ok=True)
    _origine_essai(nom).unlink(missing_ok=True)


def _defaut(nom):
    return _lire(MODELES_PAR_DEFAUT / fichier(nom))


def _objet_essai(nom):
    return apps.get_model(MODELES[nom][1]).objects.order_by("-modifie_le").first()


def verifier_modele(nom, contenu):
    """Raison pour laquelle le modèle ne peut pas servir, ou None."""
    if len(contenu.encode("utf-8")) > TAILLE_MAX:
        return "Le texte du modèle est trop long."
    try:
        variables = gabarits.variables(contenu)
    except gabarits.ModeleInvalide as erreur:
        return str(erreur)
    except TemplateError:
        return "Le texte du modèle n'a pas pu être lu : recharger la page."
    if not gabarits.texte(gabarits.nettoyer(contenu)).strip():
        return "Le modèle est vide."
    inconnues = variables - gabarits.variables(_defaut(nom))
    if inconnues:
        return ("Données inconnues ou placées hors de leur bloc « Pour chaque » : "
                + ", ".join(gabarits.libelle(v) for v in sorted(inconnues)) + ".")
    perimees = defauts_modele(nom, contenu)
    if perimees:
        return perimees[0]
    objet = _objet_essai(nom)
    if objet is not None:
        # Remplissage d'essai avec le dernier document de ce type.
        try:
            gabarits.remplir(contenu, objet.contexte_document())
        except Exception as erreur:
            return f"Le remplissage d'essai échoue ({erreur})."
    return None


def mettre_en_service(nom, contenu, origine, utilisateur):
    """Remplace le modèle en service ; l'ancien devient la version
    précédente. Sans contenu, revient au modèle d'origine."""
    depose, precedent = _depose(nom), _precedent(nom)
    precedent.parent.mkdir(parents=True, exist_ok=True)
    ancien = lire_modele(nom)
    if contenu is None:
        depose.unlink(missing_ok=True)
    else:
        depose.parent.mkdir(parents=True, exist_ok=True)
        depose.write_text(contenu, encoding="utf-8")
    precedent.write_text(ancien, encoding="utf-8")
    Modele.objects.update_or_create(nom=nom, defaults={
        "origine": origine, "mis_en_service_le": timezone.now(), "mis_en_service_par": utilisateur,
    })


def _a_relire(etat):
    """Jamais relu, relu il y a plus d'un an, ou remplacé depuis."""
    relu_le = etat.relu_le
    return (
        relu_le is None or timezone.localdate() - relu_le > registre_regles.VALIDITE
        or (etat.mis_en_service_le is not None and timezone.localdate(etat.mis_en_service_le) > relu_le)
    )


def _minuscule(texte):
    return texte[:1].lower() + texte[1:]


def _sans_renvoi(message):
    # Sans le renvoi vers « Modèles des documents » : on y est déjà.
    return message.split(" Corriger le modèle")[0]


def _etat_legal(nom, groupes):
    """Défauts du modèle en service, la raison courte à montrer dans la
    liste, les règles qui s'appliquent et si une correction est proposée."""
    defauts = defauts_modele(nom)
    lignes = groupes.get(DOCUMENT_REGLES[nom], [])
    propres = [ligne for ligne in lignes if getattr(ligne["regle"], "modele", "") == nom
               and ligne["etat"] == registre_regles.A_CORRIGER]
    if propres:
        raison = f"{propres[0]['regle'].libelle} : {propres[0]['regle'].applique.lower()}"
    else:
        raison = _sans_renvoi(defauts[0]) if defauts else ""
    return {
        "a_corriger": bool(defauts), "raison": raison, "defauts": [_sans_renvoi(defaut) for defaut in defauts],
        "regles": lignes, "corrigeable": any(ligne["corrigeable"] for ligne in propres),
    }


def modeles(request):
    if not est_administrateur(request.user):
        raise PermissionDenied
    etats = {modele.nom: modele for modele in Modele.objects.select_related("mis_en_service_par", "relu_par")}
    groupes = registre_regles.registre(timezone.localdate())
    liste = []
    for nom, (libelle, _) in MODELES.items():
        etat = etats.get(nom) or Modele(nom=nom)
        liste.append({"nom": nom, "libelle": libelle, "etat": etat, "personnalise": _depose(nom).exists(),
                      "a_relire": _a_relire(etat), "legal": _etat_legal(nom, groupes)})
    return render(request, "documents/modeles.html", {"modeles": liste})


def modele(request, nom):
    """Page d'un modèle : son état légal, les étapes pour en changer le
    texte (reprise, essai, mise en service avec la relecture) et le reste."""
    _verifier(request, nom)
    etat = Modele.objects.select_related("mis_en_service_par", "relu_par").filter(nom=nom).first() or Modele(nom=nom)
    essai = _essai(nom)
    en_attente = essai.exists()
    return render(request, "documents/modele.html", {
        "nom": nom, "libelle": MODELES[nom][0], "etat": etat, "a_relire": _a_relire(etat),
        "legal": _etat_legal(nom, registre_regles.registre(timezone.localdate())),
        "personnalise": _depose(nom).exists(), "precedent": _precedent(nom).exists(),
        "avec_donnees": _objet_essai(nom) is not None, "en_attente": en_attente,
        "origine_essai": _minuscule(Modele.Origine(_origine_essai(nom).read_text()).label)
        if en_attente and _origine_essai(nom).exists() else "",
        "essai_vu": en_attente and request.session.get(_cle_version(nom)) == _empreinte(_lire(essai)),
        "reference": etat.relu_reference or REFERENCE_RELECTURE[nom],
    })


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
        return redirect("documents:modele", nom=nom)
    Modele.objects.update_or_create(nom=nom, defaults={
        "relu_le": timezone.localdate(), "relu_par": request.user, "relu_reference": reference,
    })
    noter(request, "modèle relu", MODELES[nom][0], reference)
    messages.success(request, f"{MODELES[nom][0]} : relecture enregistrée.")
    return redirect("documents:modele", nom=nom)


def _correction(nom):
    """(contenu corrigé, changements, empreinte du modèle en service) : la
    correction vue en essai reste celle qu'on met en service tant que le
    modèle n'a pas changé entre-temps."""
    source = lire_modele(nom)
    contenu, changements = corriger_modele(nom, source)
    return contenu, changements, _empreinte(source)


def _cle_essai(nom):
    return f"essai_correction:{nom}"


def corriger(request, nom):
    """Correction proposée d'un modèle aux clauses périmées ou aux mentions
    obligatoires manquantes : avant, après,
    document d'essai, puis mise en service par l'administrateur."""
    _verifier(request, nom)
    _, changements, empreinte = _correction(nom)
    if not changements:
        messages.info(request, f"Aucune correction à proposer pour « {MODELES[nom][0]} ».")
        return redirect("documents:modele", nom=nom)
    lignes = [{**changement, "message": _sans_renvoi(changement["message"])} for changement in changements]
    return render(request, "documents/corriger.html", {
        "nom": nom, "libelle": MODELES[nom][0], "lignes": lignes,
        "essai_vu": request.session.get(_cle_essai(nom)) == empreinte,
        "avec_donnees": _objet_essai(nom) is not None,
    })


def _document_essai(request, nom, contenu):
    """PDF d'essai d'une version du modèle, rempli avec le dernier document
    de ce type (sans dossier enregistré, avec les seules données de
    l'agence) ; la page HTML si le PDF ne peut pas être produit."""
    from core.models import Organisation

    objet = _objet_essai(nom)
    contexte = objet.contexte_document() if objet is not None else {"agence": Organisation.courante().contexte_document()}
    page = generer_html(nom, contexte, source=contenu)
    try:
        return HttpResponse(convertir_pdf(page.encode("utf-8"), nom), content_type="application/pdf", headers={
            "Content-Disposition": f'inline; filename="essai-{nom}.pdf"'})
    except ConversionImpossible:
        return HttpResponse(page, content_type=f"{HTML}; charset=utf-8")


def essai_correction(request, nom):
    """Document d'essai produit avec le modèle corrigé ; l'ouvrir débloque la
    mise en service de cette correction-là."""
    _verifier(request, nom)
    contenu, changements, empreinte = _correction(nom)
    if not changements:
        return redirect("documents:modele", nom=nom)
    request.session[_cle_essai(nom)] = empreinte
    return _document_essai(request, nom, contenu)


@require_POST
def appliquer_correction(request, nom):
    _verifier(request, nom)
    contenu, changements, empreinte = _correction(nom)
    libelle = MODELES[nom][0]
    if not changements:
        return redirect("documents:modele", nom=nom)
    if request.session.get(_cle_essai(nom)) != empreinte:
        messages.error(request, "Ouvrir d'abord le document d'essai de la correction.")
        return redirect("documents:corriger", nom=nom)
    raison = verifier_modele(nom, contenu)
    if raison:
        messages.error(request, f"« {libelle} » n'a pas été corrigé. {raison}")
        return redirect("documents:corriger", nom=nom)
    mettre_en_service(nom, contenu, Modele.Origine.EDITEUR, request.user)
    remplacements = [f"« {c['ajoute']} » au lieu de « {c['retire']} »" for c in changements if c["retire"]]
    if len(remplacements) < len(changements):
        remplacements.append("mentions obligatoires reprises du modèle d'origine")
    reference = "; ".join(remplacements)
    Modele.objects.filter(nom=nom).update(
        relu_le=timezone.localdate(), relu_par=request.user, relu_reference=f"la correction proposée ({reference})"[:200])
    del request.session[_cle_essai(nom)]
    noter(request, "modèle corrigé", nom, reference)
    messages.success(request, f"« {libelle} » corrigé et mis en service ; la version précédente reste récupérable.")
    return redirect("documents:modele", nom=nom)


def essai(request, nom):
    """Dernier document de ce type, produit avec le modèle en service."""
    _verifier(request, nom)
    objet = _objet_essai(nom)
    if objet is None:
        messages.error(request, f"Aucun {MODELES[nom][0].lower()} enregistré pour faire un essai.")
        return redirect("documents:modele", nom=nom)
    return document(request, objet, nom, "pdf")


def essai_version(request, nom):
    """Document d'essai de la version en attente (le dernier document de ce
    type rempli avec elle, ou à défaut la version elle-même) ; l'ouvrir
    débloque sa mise en service."""
    _verifier(request, nom)
    if not _essai(nom).exists():
        return redirect("documents:modele", nom=nom)
    contenu = _lire(_essai(nom))
    request.session[_cle_version(nom)] = _empreinte(contenu)
    return _document_essai(request, nom, contenu)


def texte(request, nom):
    """Éditeur intégré : le texte du modèle (ou de la version en attente
    d'essai) modifiable sur place ; l'enregistrement prépare un essai."""
    _verifier(request, nom)
    libelle = MODELES[nom][0]
    en_attente = _essai(nom).exists()
    source = _lire(_essai(nom)) if en_attente else lire_modele(nom)
    alertes = [(extrait, _sans_renvoi(message)) for extrait, message in CLAUSES_PERIMEES.get(nom, {}).items()]
    if request.method == "POST":
        envoye = request.POST.get("texte", "")
        try:
            contenu = gabarits.normaliser(envoye)
        except gabarits.ModeleInvalide as erreur:
            raison = str(erreur)
        else:
            raison = verifier_modele(nom, contenu)
            source = contenu
        if raison:
            messages.error(request, f"Le texte n'a pas été enregistré. {_sans_renvoi(raison)}")
        else:
            preparer_essai(nom, contenu, Modele.Origine.EDITEUR)
            noter(request, "modèle modifié", libelle, "dans l'application")
            messages.success(request, "Texte enregistré. Ouvrez le document d'essai, puis mettez en service.")
            return redirect("documents:modele", nom=nom)
    defaut = gabarits.nettoyer(_defaut(nom))
    arbre = gabarits.nettoyer(source)
    return render(request, "documents/texte.html", {
        "nom": nom, "libelle": libelle, "en_attente": en_attente,
        "document_editeur": gabarits.pour_editeur(arbre, alertes),
        "sommaire": gabarits.titres(arbre),
        "donnees": [(code, gabarits.libelle(code)) for code in gabarits.expressions(defaut, "data-donnee")],
        "conditions": [(code, gabarits.libelle_condition({"data-si": code})) for code in gabarits.expressions(defaut, "data-si")],
        "corrigeable": bool(corriger_modele(nom, source)[1]),
    })


@require_POST
def mettre_en_service_essai(request, nom):
    """Met en service la version dont l'essai a été ouvert, et note la
    relecture si l'administrateur la coche."""
    _verifier(request, nom)
    libelle, essai = MODELES[nom][0], _essai(nom)
    if not essai.exists():
        return redirect("documents:modele", nom=nom)
    contenu = _lire(essai)
    if request.session.get(_cle_version(nom)) != _empreinte(contenu):
        messages.error(request, "Ouvrir d'abord le document d'essai.")
        return redirect("documents:modele", nom=nom)
    reference = " ".join(request.POST.get("reference", "").split())[:200]
    if request.POST.get("relu") and not reference:
        messages.error(request, "Indiquer d'après quels textes le modèle a été relu.")
        return redirect("documents:modele", nom=nom)
    raison = verifier_modele(nom, contenu)
    if raison:
        messages.error(request, f"« {libelle} » n'a pas été mis en service. {raison}")
        return redirect("documents:modele", nom=nom)
    origine = _origine_essai(nom).read_text() if _origine_essai(nom).exists() else Modele.Origine.EDITEUR
    mettre_en_service(nom, contenu, origine, request.user)
    abandonner_essai(nom)
    del request.session[_cle_version(nom)]
    noter(request, "modèle remplacé", nom, _minuscule(Modele.Origine(origine).label))
    if request.POST.get("relu"):
        Modele.objects.filter(nom=nom).update(relu_le=timezone.localdate(), relu_par=request.user, relu_reference=reference)
        noter(request, "modèle relu", libelle, reference)
        messages.success(request, f"« {libelle} » mis en service, relecture notée. La version précédente reste récupérable.")
    else:
        messages.success(request, f"« {libelle} » mis en service, à relire. La version précédente reste récupérable.")
    return redirect("documents:modele", nom=nom)


@require_POST
def abandonner(request, nom):
    _verifier(request, nom)
    abandonner_essai(nom)
    messages.info(request, "Essai abandonné : le modèle en service n'a pas changé.")
    return redirect("documents:modele", nom=nom)


@require_POST
def precedent(request, nom):
    _verifier(request, nom)
    if not _precedent(nom).exists():
        raise Http404
    mettre_en_service(nom, _lire(_precedent(nom)), Modele.Origine.PRECEDENT, request.user)
    noter(request, "modèle remplacé", nom, "version précédente")
    messages.success(request, f"Version précédente de « {MODELES[nom][0]} » remise en service.")
    return redirect("documents:modele", nom=nom)


@require_POST
def retablir(request, nom):
    _verifier(request, nom)
    mettre_en_service(nom, None, Modele.Origine.ORIGINE, request.user)
    noter(request, "modèle remplacé", nom, "modèle d'origine")
    messages.success(request, f"Modèle d'origine « {MODELES[nom][0]} » rétabli.")
    return redirect("documents:modele", nom=nom)
