"""Ce que les contrats de l'agence ont en commun.

Un contrat est soit un bail d'habitation (contrats.Bail), soit un mandat de
gestion (mandats.Mandat). Chacun garde sa table ; cette base abstraite porte
la date de signature et l'interface que la liste des contrats et la fiche
d'une propriété utilisent sans distinguer les deux.
"""

from django.db import models

from proprietes.models import Horodatage


class Contrat(Horodatage):
    #: « Bail » ou « Mandat », pour l'affichage et les filtres.
    TYPE = ""

    date_signature = models.DateField("date de signature", null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def code_type(self):
        return self.TYPE.lower()

    @property
    def signe(self):
        return self.date_signature is not None

    @property
    def parties(self):
        """Cocontractants de l'agence ou du bailleur : locataires ou mandant."""
        raise NotImplementedError

    @property
    def debut(self):
        raise NotImplementedError

    @property
    def echeance(self):
        """(date de fin ou None, précision à afficher sous la date)."""
        raise NotImplementedError

    @property
    def etat(self):
        """(code de pastille, libellé)."""
        raise NotImplementedError
