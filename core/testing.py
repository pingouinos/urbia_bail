"""Comptes métier explicites pour les tests des parcours de gestion."""
from django.contrib.auth.models import User
from comptes.roles import GESTIONNAIRES, attribuer_role


def creer_gestionnaire(*args, **kwargs):
    user = User.objects.create_user(*args, **kwargs)
    attribuer_role(user, GESTIONNAIRES)
    return user
