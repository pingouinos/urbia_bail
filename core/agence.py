"""Agence pour laquelle tourne le code en cours : celle du compte connecté
pendant une requête, ou celle qu'une commande a choisie avec `activer`.

Une variable de contexte et non un attribut de la requête : les modèles, les
formulaires et les gabarits la lisent sans qu'on la leur passe.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_agence = ContextVar("agence", default=None)


def agence_active():
    """L'agence en contexte, ou None."""
    return _agence.get()


@contextmanager
def activer(agence):
    jeton = _agence.set(agence)
    try:
        yield agence
    finally:
        _agence.reset(jeton)
