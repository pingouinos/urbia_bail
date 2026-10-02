"""Connexion à l'annuaire du NAS Synology.

Deux paquets Synology peuvent faire office d'annuaire :

* « Synology Directory Server » : un domaine compatible Active Directory
  (LDAP_GROUP_TYPE=ad, identifiant = sAMAccountName) ;
* « LDAP Server » : un annuaire OpenLDAP (LDAP_GROUP_TYPE=posix,
  identifiant = uid).
"""

import ldap
from django_auth_ldap.config import (
    LDAPSearch,
    NestedActiveDirectoryGroupType,
    PosixGroupType,
)

TYPES_ANNUAIRE = {
    "ad": {
        "filtre_utilisateur": "(sAMAccountName=%(user)s)",
        "filtre_groupe": "(objectClass=group)",
        "type_groupe": NestedActiveDirectoryGroupType,
    },
    "posix": {
        "filtre_utilisateur": "(uid=%(user)s)",
        "filtre_groupe": "(objectClass=posixGroup)",
        "type_groupe": PosixGroupType,
    },
}


def configurer_ldap(env):
    """Renvoie les réglages django-auth-ldap tirés des variables d'environnement."""
    type_annuaire = env("LDAP_GROUP_TYPE")
    if type_annuaire not in TYPES_ANNUAIRE:
        raise ValueError(
            f"LDAP_GROUP_TYPE doit valoir {' ou '.join(TYPES_ANNUAIRE)}, pas {type_annuaire!r}"
        )
    params = TYPES_ANNUAIRE[type_annuaire]

    base_utilisateurs = env("LDAP_USER_BASE_DN")
    base_groupes = env("LDAP_GROUP_BASE_DN", default=base_utilisateurs)

    return {
        "AUTH_LDAP_SERVER_URI": env("LDAP_SERVER_URI"),
        "AUTH_LDAP_BIND_DN": env("LDAP_BIND_DN", default=""),
        "AUTH_LDAP_BIND_PASSWORD": env("LDAP_BIND_PASSWORD", default=""),
        "AUTH_LDAP_START_TLS": env("LDAP_START_TLS"),
        "AUTH_LDAP_CONNECTION_OPTIONS": {
            ldap.OPT_REFERRALS: 0,
            ldap.OPT_NETWORK_TIMEOUT: 5,
        },
        "AUTH_LDAP_USER_SEARCH": LDAPSearch(
            base_utilisateurs,
            ldap.SCOPE_SUBTREE,
            env("LDAP_USER_FILTER", default=params["filtre_utilisateur"]),
        ),
        "AUTH_LDAP_GROUP_SEARCH": LDAPSearch(
            base_groupes, ldap.SCOPE_SUBTREE, params["filtre_groupe"]
        ),
        "AUTH_LDAP_GROUP_TYPE": params["type_groupe"](),
        # Seuls les membres de ce groupe de l'annuaire ont accès à l'application.
        "AUTH_LDAP_REQUIRE_GROUP": env("LDAP_GROUPE_ACCES"),
        "AUTH_LDAP_USER_ATTR_MAP": {
            "first_name": "givenName",
            "last_name": "sn",
            "email": "mail",
        },
        "AUTH_LDAP_ALWAYS_UPDATE_USER": True,
        "AUTH_LDAP_FIND_GROUP_PERMS": False,
        "AUTH_LDAP_CACHE_TIMEOUT": 300,
        # Groupe de l'annuaire dont les membres deviennent administrateurs.
        "LDAP_GROUPE_ADMIN": env("LDAP_GROUPE_ADMIN"),
    }
