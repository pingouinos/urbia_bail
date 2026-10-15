"""Comptes métier explicites pour les tests des parcours de gestion."""
from django.contrib.auth.models import User
from comptes.roles import GESTIONNAIRES, attribuer_role


def creer_gestionnaire(*args, **kwargs):
    user = User.objects.create_user(*args, **kwargs)
    attribuer_role(user, GESTIONNAIRES)
    return user


AGENCE = {
    "nom": "Agence du Canal", "adresse": "3 quai de Brienne", "code_postal": "31000", "ville": "Toulouse",
    "carte_pro": "CPI 3101 2020 000 000 001", "carte_delivree_par": "la CCI de Toulouse",
    "garant": "GARANTIE SA", "garant_adresse": "2 rue de la Paix, 75002 Paris", "garantie_montant": 120000,
    "rcp": "MMA, contrat n° 123456", "mediateur": "Médiateur de l'immobilier, 75000 Paris",
    "mediateur_site": "https://mediateur.example", "orias": "12345678", "assurance_gli": "SADA Assurances",
    "assurance_gli_contrat": "1P0012207",
}


def remplir_agence(**autres):
    """Fiche de l'agence avec toutes les mentions légales (loi Hoguet,
    code de la consommation)."""
    from core.models import Organisation

    organisation = Organisation.courante()
    for champ, valeur in {**AGENCE, **autres}.items():
        setattr(organisation, champ, valeur)
    organisation.save()
    return organisation
