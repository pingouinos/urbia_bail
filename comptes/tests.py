from unittest import mock

import environ
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from comptes.ldap import configurer_ldap
from comptes.roles import ADMINISTRATEURS, GESTIONNAIRES, attribuer_role, est_administrateur


class RolesTests(TestCase):
    def test_les_deux_roles_existent_apres_migration(self):
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)), {ADMINISTRATEURS, GESTIONNAIRES}
        )

    def test_attribuer_role_garde_un_seul_role_et_aligne_is_staff(self):
        user = User.objects.create_user("elodie")
        attribuer_role(user, ADMINISTRATEURS)
        self.assertTrue(user.is_staff)
        self.assertTrue(est_administrateur(user))

        attribuer_role(user, GESTIONNAIRES)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertEqual(list(user.groups.values_list("name", flat=True)), [GESTIONNAIRES])

    def test_role_inconnu_refuse(self):
        with self.assertRaises(ValueError):
            attribuer_role(User.objects.create_user("x"), "Stagiaires")


class ConnexionTests(TestCase):
    def test_page_protegee_redirige_vers_connexion(self):
        reponse = self.client.get(reverse("accueil"))
        self.assertRedirects(reponse, f"{reverse('connexion')}?next=/")

    def test_connexion_puis_accueil(self):
        User.objects.create_user("gestion", password="mot-de-passe-solide")
        reponse = self.client.post(
            reverse("connexion"), {"username": "gestion", "password": "mot-de-passe-solide"}
        )
        self.assertRedirects(reponse, reverse("accueil"))
        self.assertContains(self.client.get(reverse("accueil")), "gestionnaire")

    def test_sonde_sante_publique(self):
        reponse = self.client.get(reverse("sante"))
        self.assertEqual(reponse.json(), {"statut": "ok"})


class ConfigurationLdapTests(TestCase):
    VARIABLES = {
        "LDAP_SERVER_URI": "ldap://nas.agence.local",
        "LDAP_USER_BASE_DN": "cn=users,dc=agence,dc=local",
        "LDAP_GROUPE_ACCES": "cn=urbiabail,cn=users,dc=agence,dc=local",
        "LDAP_GROUPE_ADMIN": "cn=urbiabail-admin,cn=users,dc=agence,dc=local",
    }

    def env(self, **autres):
        with mock.patch.dict("os.environ", {**self.VARIABLES, **autres}, clear=True):
            return configurer_ldap(
                environ.Env(LDAP_START_TLS=(bool, False), LDAP_GROUP_TYPE=(str, "ad"))
            )

    def test_active_directory_par_defaut(self):
        reglages = self.env()
        self.assertEqual(reglages["AUTH_LDAP_USER_SEARCH"].filterstr, "(sAMAccountName=%(user)s)")
        self.assertEqual(reglages["AUTH_LDAP_REQUIRE_GROUP"], self.VARIABLES["LDAP_GROUPE_ACCES"])

    def test_annuaire_posix(self):
        reglages = self.env(LDAP_GROUP_TYPE="posix")
        self.assertEqual(reglages["AUTH_LDAP_USER_SEARCH"].filterstr, "(uid=%(user)s)")

    def test_type_inconnu_refuse(self):
        with self.assertRaises(ValueError):
            self.env(LDAP_GROUP_TYPE="novell")


@override_settings(LDAP_ENABLED=True, LDAP_GROUPE_ADMIN="CN=Admin,DC=agence")
class RoleDepuisAnnuaireTests(TestCase):
    def test_membre_du_groupe_admin_devient_administrateur(self):
        from comptes.signals import appliquer_role_annuaire_impl

        user = User.objects.create_user("elodie")
        appliquer_role_annuaire_impl(user, ["cn=admin,dc=agence", "cn=autre,dc=agence"])
        self.assertTrue(est_administrateur(user))

        appliquer_role_annuaire_impl(user, ["cn=autre,dc=agence"])
        self.assertFalse(est_administrateur(user))
