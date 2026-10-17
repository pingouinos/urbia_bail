"""Connexions et déconnexions inscrites au journal de sécurité, et
rattachement des nouveaux comptes à leur agence."""

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
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


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def rattacher_compte(sender, instance, created, raw=False, **kwargs):
    """Un nouveau compte appartient à l'agence de qui le crée."""
    from .models import Membre, Organisation

    if created and not raw:
        Membre.objects.get_or_create(utilisateur=instance, defaults={"organisation": Organisation.courante()})
