"""Agence pour laquelle tourne le code en cours, et filtre automatique des
données par agence.

Pendant une requête, `core.middleware.AgenceMiddleware` pose l'agence du
compte connecté. Chaque modèle qui appartient à une agence (`RattacheAgence`,
ou un enfant qui déclare `chemins_agence`) ne renvoie alors, par son manager
`objects`, que les données de cette agence. Le filtre échoue fermé : une
requête sans agence (visiteur anonyme, superutilisateur hors de la console)
lève `AgenceInconnue` au lieu de tout renvoyer. Le manager `tous` lit toutes
les agences ; chacun de ses usages se justifie dans le code.

Hors requête (commandes, shell, tests), aucune agence n'est posée : le filtre
laisse tout passer, comme avant le multi-agences.

Une variable de contexte et non un attribut de la requête : les modèles, les
formulaires et les gabarits la lisent sans qu'on la leur passe.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Q

# Portées qui ne sont pas une agence.
TOUTES = "toutes"  # hors requête, ou console du superutilisateur
AUCUNE = "aucune"  # requête sans agence : rien n'est lisible

_portee = ContextVar("agence", default=TOUTES)


class AgenceInconnue(PermissionDenied):
    """Lecture ou écriture de données d'agence sans agence en contexte."""


def portee():
    return _portee.get()


def agence_active():
    """L'agence en contexte, ou None."""
    valeur = _portee.get()
    return None if isinstance(valeur, str) else valeur


@contextmanager
def activer(agence):
    """Pose une agence, ou les portées TOUTES et AUCUNE (None vaut AUCUNE)."""
    jeton = _portee.set(AUCUNE if agence is None else agence)
    try:
        yield agence
    finally:
        _portee.reset(jeton)


def agence_pour_ecrire():
    """L'agence à laquelle rattacher une donnée créée maintenant."""
    valeur = _portee.get()
    if valeur == AUCUNE:
        raise AgenceInconnue("Aucune agence en contexte pour enregistrer cette donnée.")
    if valeur == TOUTES:
        return apps.get_model("core", "Organisation").premiere()
    return valeur


class ParAgenceManager(models.Manager):
    """Manager par défaut des données d'agence : filtré sur l'agence en
    contexte, par les chemins que déclare le modèle."""

    def get_queryset(self):
        queryset = super().get_queryset()
        valeur = _portee.get()
        if valeur == TOUTES:
            return queryset
        if valeur == AUCUNE:
            raise AgenceInconnue(f"Lecture de {self.model._meta.verbose_name_plural} sans agence en contexte.")
        filtre = Q()
        for chemin in self.model.chemins_agence:
            filtre |= Q(**{chemin: valeur})
        return queryset.filter(filtre)


class ParAgence(models.Model):
    """Enfant d'une donnée d'agence, filtré par son parent (par exemple un
    locataire par son bail). `chemins_agence` mène à l'agence ; plusieurs
    chemins se combinent en « ou »."""

    chemins_agence = ()

    objects = ParAgenceManager()
    tous = models.Manager()

    class Meta:
        abstract = True


class RattacheAgence(ParAgence):
    """Donnée qui appartient à une agence. L'agence se remplit à la création
    avec celle du contexte."""

    chemins_agence = ("agence",)

    agence = models.ForeignKey(
        "core.Organisation", on_delete=models.PROTECT, related_name="+", editable=False, verbose_name="agence",
    )

    class Meta:
        abstract = True

    def _rattacher(self):
        if not self.agence_id:
            self.agence = agence_pour_ecrire()

    def save(self, *args, **kwargs):
        self._rattacher()
        super().save(*args, **kwargs)

    # Les formulaires n'ont pas le champ agence : sans ces deux méthodes, ils
    # ne vérifieraient pas les unicités propres à chaque agence (numéro de
    # mandat, n° de lot…) et l'enregistrement échouerait en base.
    def validate_unique(self, exclude=None):
        self._rattacher()
        super().validate_unique(exclude=_sans_agence(exclude))

    def validate_constraints(self, exclude=None):
        self._rattacher()
        super().validate_constraints(exclude=_sans_agence(exclude))


def _sans_agence(exclude):
    return None if exclude is None else set(exclude) - {"agence"}
