from django.conf import settings
from django.dispatch import receiver

from .roles import ADMINISTRATEURS, GESTIONNAIRES, attribuer_role


def appliquer_role_annuaire_impl(user, group_dns):
    """Le rôle suit l'appartenance au groupe administrateur de l'annuaire."""
    if user.pk is None:
        user.save()
    groupe_admin = settings.LDAP_GROUPE_ADMIN.lower()
    est_admin = groupe_admin in {dn.lower() for dn in group_dns}
    attribuer_role(user, ADMINISTRATEURS if est_admin else GESTIONNAIRES)


if settings.LDAP_ENABLED:
    from django_auth_ldap.backend import populate_user

    @receiver(populate_user)
    def appliquer_role_annuaire(sender, user, ldap_user, **kwargs):
        appliquer_role_annuaire_impl(user, ldap_user.group_dns)
