"""Connexion des collaborateurs par leur compte Google Workspace."""

import logging

from django.conf import settings
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .roles import GESTIONNAIRES, attribuer_role

logger = logging.getLogger(__name__)

BACKEND = "comptes.google.GoogleWorkspaceBackend"


class GoogleWorkspaceBackend(OIDCAuthenticationBackend):
    def verify_claims(self, claims):
        """N'accepte qu'une adresse vérifiée du domaine Workspace de l'agence."""
        domaine = settings.GOOGLE_DOMAINE.lower()
        email = (claims.get("email") or "").lower()
        valide = (
            bool(domaine)
            and claims.get("email_verified") is True
            and (claims.get("hd") or "").lower() == domaine
            and email.endswith(f"@{domaine}")
        )
        if not valide:
            logger.warning("Connexion Google refusée pour %s (hors domaine ou non vérifiée)", email)
        return valide

    def filter_users_by_claims(self, claims):
        return self.UserModel.objects.filter(email__iexact=claims["email"])

    def create_user(self, claims):
        """Création à la première connexion (si GOOGLE_CREATION_AUTO) : gestionnaire."""
        user = self.UserModel.objects.create_user(
            username=claims["email"].lower(),
            email=claims["email"].lower(),
            first_name=claims.get("given_name", ""),
            last_name=claims.get("family_name", ""),
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        attribuer_role(user, GESTIONNAIRES)
        logger.info("Compte créé à la première connexion Google : %s", user.email)
        return user

    def update_user(self, user, claims):
        user.first_name = claims.get("given_name", user.first_name)
        user.last_name = claims.get("family_name", user.last_name)
        user.save(update_fields=["first_name", "last_name"])
        return user

    def get_user(self, user_id):
        """Un compte désactivé perd l'accès immédiatement, même en pleine session."""
        user = super().get_user(user_id)
        return user if user and user.is_active else None
