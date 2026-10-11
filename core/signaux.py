"""Connexions et déconnexions inscrites au journal de sécurité."""

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .journal import noter


@receiver(user_logged_in)
def connexion(sender, request, user, **kwargs):
    noter(request, "connexion", identifiant=user.get_username())


@receiver(user_logged_out)
def deconnexion(sender, request, user, **kwargs):
    if user is not None:
        noter(request, "déconnexion", identifiant=user.get_username())


@receiver(user_login_failed)
def connexion_refusee(sender, credentials, request=None, **kwargs):
    if request is not None:
        noter(request, "connexion refusée", identifiant=str(credentials.get("username", ""))[:150])
