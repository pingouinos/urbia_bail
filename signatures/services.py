"""Envoi d'un bail ou d'un mandat en signature électronique, suivi des
signataires et rangement du PDF signé.

Les signataires se déduisent du contrat : les locataires (ou le mandant),
puis l'agence, représentée par le collaborateur qui envoie, en dernier. Le
document est produit avec le modèle Word en service, dont les balises
signature_… deviennent des emplacements de signature DocuSeal. Une fois tout
le monde passé, la date de signature se remplit et le PDF signé est rangé
sur le contrat : l'étape Signature se clôt d'elle-même.
"""

import io
import zipfile

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import Organisation
from documents.generation import ConversionImpossible, convertir_pdf, generer_docx

from . import docuseal
from .models import DemandeSignature, Signataire

AGENCE = "Agence"
# Taille des emplacements de signature, en pixels.
LARGEUR, HAUTEUR = 170, 55


class SignatureImpossible(Exception):
    """L'envoi ou le suivi ne peut pas se faire ; le message dit pourquoi, en clair."""


def balise(nom, role, type_="signature", **options):
    """Balise de champ DocuSeal, écrite telle quelle dans le PDF."""
    attributs = [nom, f"type={type_}", f"role={role}", *(f"{cle}={valeur}" for cle, valeur in options.items())]
    return "{{" + ";".join(attributs) + "}}"


def _signature(role):
    return balise(f"Signature {role}", role, width=LARGEUR, height=HAUTEUR)


def est_bail(contrat):
    return contrat.TYPE == "Bail"


def modele(contrat):
    return "bail_habitation.docx" if est_bail(contrat) else "mandat_gestion.docx"


def signataires_prevus(contrat, utilisateur):
    """Qui signe, dans l'ordre : une liste de dict role, qualite, nom, email."""
    bailleur = contrat.propriete.bailleur
    if est_bail(contrat):
        liste = [
            {"role": f"Locataire {rang}", "qualite": "locataire", "nom": str(locataire), "email": locataire.email}
            for rang, locataire in enumerate(contrat.locataires.all(), start=1)
        ]
        pour = f"pour le bailleur, {bailleur.designation}"
    else:
        liste = [{"role": "Mandant", "qualite": "mandant", "nom": contrat.mandant, "email": bailleur.email}]
        pour = "mandataire"
    agence = Organisation.courante().nom or "l'agence"
    nom = utilisateur.get_full_name() or utilisateur.get_username()
    liste.append({"role": AGENCE, "qualite": f"{agence}, {pour}", "nom": nom, "email": utilisateur.email})
    return liste


def manques(contrat, utilisateur):
    """Ce qui empêche l'envoi, en phrases prêtes à afficher."""
    if not docuseal.configure():
        return ["La signature électronique n'est pas encore configurée sur ce serveur."]
    if contrat.signe:
        return ["Ce contrat est déjà signé."]
    if en_cours(contrat):
        return ["Ce contrat est déjà chez les signataires."]
    liste = []
    prevus = signataires_prevus(contrat, utilisateur)
    if est_bail(contrat) and len(prevus) == 1:
        liste.append("Ajoutez au moins un locataire au bail.")
    for signataire in prevus:
        if not signataire["email"]:
            if signataire["role"] == AGENCE:
                liste.append("Ajoutez votre adresse e-mail à votre compte : vous signez pour l'agence.")
            else:
                liste.append(f"Ajoutez l'e-mail de {signataire['nom']}, {signataire['qualite']}.")
    return liste


def contexte_signature(contrat, prevus):
    """Balises à placer dans le document envoyé : un emplacement par
    signataire, et la date de la dernière signature."""
    roles = [signataire["role"] for signataire in prevus]
    contexte = {
        "signature_mandataire": _signature(AGENCE),
        "date_signature": balise("Date", AGENCE, type_="datenow", readonly="true"),
    }
    if est_bail(contrat):
        contexte["signatures_locataires"] = [_signature(role) for role in roles if role != AGENCE]
    else:
        contexte["signature_mandant"] = _signature("Mandant")
    return contexte


def _texte_docx(contenu):
    with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
        return archive.read("word/document.xml").decode("utf-8", "replace")


def en_cours(contrat):
    return contrat.demandes_signature.filter(statut=DemandeSignature.Statut.ENVOYEE).first()


def envoyer(contrat, utilisateur):
    """Produit le PDF, l'envoie dans DocuSeal et enregistre la demande."""
    raisons = manques(contrat, utilisateur)
    if raisons:
        raise SignatureImpossible(" ".join(raisons))
    prevus = signataires_prevus(contrat, utilisateur)
    contenu = generer_docx(modele(contrat), contrat.contexte_document() | contexte_signature(contrat, prevus))
    texte = _texte_docx(contenu)
    absents = [s["nom"] for s in prevus if f"role={s['role']};" not in texte]
    if absents:
        raise SignatureImpossible(
            "Le modèle Word en service n'a pas d'emplacement de signature pour "
            + ", ".join(absents) + " : un administrateur doit y remettre les balises signature_… "
            "(voir le modèle d'origine, page Modèles)."
        )
    try:
        pdf = convertir_pdf(contenu, modele(contrat))
    except ConversionImpossible as erreur:
        raise SignatureImpossible(f"Le PDF n'a pas pu être produit ({erreur}).") from erreur
    try:
        crees = docuseal.creer_demande(
            contrat.nom_fichier,
            pdf,
            [{"role": s["role"], "name": s["nom"], "email": s["email"]} for s in prevus],
        )
    except docuseal.DocusealIndisponible as erreur:
        raise SignatureImpossible(f"L'envoi a échoué : {erreur}.") from erreur
    par_role = {signataire.get("role"): signataire for signataire in crees}
    with transaction.atomic():
        demande = DemandeSignature.objects.create(
            soumission=crees[0]["submission_id"], envoyee_par=utilisateur,
            **{"bail" if est_bail(contrat) else "mandat": contrat},
        )
        Signataire.objects.bulk_create(
            Signataire(
                demande=demande, ordre=rang, role=s["role"], qualite=s["qualite"], nom=s["nom"], email=s["email"],
                identifiant=par_role.get(s["role"], {}).get("id"),
            )
            for rang, s in enumerate(prevus, start=1)
        )
    return demande


STATUTS = {
    "completed": Signataire.Statut.SIGNE,
    "opened": Signataire.Statut.OUVERT,
    "declined": Signataire.Statut.REFUSE,
}


def _date(valeur):
    return parse_datetime(valeur) if valeur else None


def synchroniser(demande):
    """Reprend l'état de la demande dans DocuSeal. Quand tout le monde a
    signé, range le PDF signé et date la signature du contrat."""
    if not demande.en_cours:
        return demande
    donnees = docuseal.demande(demande.soumission)
    signataires = {s.identifiant: s for s in demande.signataires.all()}
    for distant in donnees.get("submitters", []):
        signataire = signataires.get(distant.get("id"))
        if signataire is None:
            continue
        signataire.statut = STATUTS.get(distant.get("status"), Signataire.Statut.EN_ATTENTE)
        signataire.signe_le = _date(distant.get("completed_at"))
        signataire.save(update_fields=["statut", "signe_le"])
    maintenant = timezone.now()
    demande.synchronisee_le = maintenant
    if donnees.get("status") == "declined" or any(
        s.statut == Signataire.Statut.REFUSE for s in signataires.values()
    ):
        demande.statut = DemandeSignature.Statut.REFUSEE
        demande.terminee_le = maintenant
    elif donnees.get("status") == "completed":
        _ranger(demande, donnees)
    demande.save(update_fields=["statut", "terminee_le", "synchronisee_le"])
    return demande


def _ranger(demande, donnees):
    url = donnees.get("combined_document_url") or next(
        (document.get("url") for document in donnees.get("documents", []) if document.get("url")), None
    )
    if not url:
        raise docuseal.DocusealIndisponible("DocuSeal n'a pas donné le lien du document signé")
    contenu = docuseal.telecharger(url)
    termine = _date(donnees.get("completed_at")) or timezone.now()
    contrat = demande.contrat
    contrat.date_signature = timezone.localdate(termine)
    contrat.fichier_signe.save(f"{contrat.nom_fichier} signé.pdf", ContentFile(contenu), save=False)
    contrat.save()
    demande.statut = DemandeSignature.Statut.SIGNEE
    demande.terminee_le = termine


def relancer(signataire):
    if not signataire.demande.en_cours or signataire.statut == Signataire.Statut.SIGNE:
        raise SignatureImpossible(f"{signataire.nom} n'a plus rien à signer.")
    try:
        docuseal.relancer(signataire.identifiant)
    except docuseal.DocusealIndisponible as erreur:
        raise SignatureImpossible(f"La relance a échoué : {erreur}.") from erreur
    signataire.relance_le = timezone.now()
    signataire.save(update_fields=["relance_le"])


def annuler(demande):
    """Les liens envoyés ne permettent plus de signer ; le contrat peut être
    corrigé puis renvoyé."""
    if not demande.en_cours:
        raise SignatureImpossible("Cette demande de signature n'est plus en cours.")
    try:
        docuseal.archiver(demande.soumission)
    except docuseal.DocusealIndisponible as erreur:
        raise SignatureImpossible(f"L'annulation a échoué : {erreur}.") from erreur
    demande.statut = DemandeSignature.Statut.ANNULEE
    demande.terminee_le = timezone.now()
    demande.save(update_fields=["statut", "terminee_le"])


# Une page ouverte relit la demande dans DocuSeal, au plus une fois par minute.
FRAICHEUR = 60


def etat_etape(contrat, utilisateur):
    """Ce que l'étape Signature affiche : la demande en cours (relue dans
    DocuSeal si elle date), ou les signataires prévus et ce qui manque."""
    if not docuseal.configure() or contrat.signe:
        return {"active": False}
    demande = en_cours(contrat)
    if demande is not None:
        vieille = demande.synchronisee_le is None or (
            timezone.now() - demande.synchronisee_le).total_seconds() > FRAICHEUR
        if vieille:
            try:
                synchroniser(demande)
            except docuseal.DocusealIndisponible:
                pass
            if not demande.en_cours:
                contrat.refresh_from_db()
                if contrat.signe:
                    return {"active": False}
                demande = None
    if demande is not None:
        return {"active": True, "demande": demande, "signataires": list(demande.signataires.all())}
    precedente = contrat.demandes_signature.exclude(statut=DemandeSignature.Statut.SIGNEE).first()
    return {
        "active": True,
        "prevus": signataires_prevus(contrat, utilisateur),
        "manques": manques(contrat, utilisateur),
        "precedente": precedente,
    }
