"""Catalogue relu dans le code : aucune URL fournie par un visiteur n'est collectée."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Reference:
    nom: str
    url: str
    perimetre: str
    modeles: tuple[str, ...]


SOURCES = {
    "bail-service-public": Reference(
        "Service Public · Rédaction du bail d'habitation",
        "https://www.service-public.gouv.fr/particuliers/vosdroits/F920",
        "France · baux d'habitation nus et meublés (dont étudiant), impact à qualifier",
        ("bail_habitation.docx",),
    ),
    "analyses-anil": Reference(
        "ANIL · Analyses juridiques",
        "https://www.anil.org/analyses-juridiques/",
        "France · analyses logement, index général à trier ; couverture des mandats non garantie",
        ("bail_habitation.docx", "mandat_gestion.docx"),
    ),
}
HOTES_AUTORISES = frozenset({"www.service-public.gouv.fr", "www.anil.org"})
