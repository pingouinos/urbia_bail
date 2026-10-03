from unittest import mock

import environ
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

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

    @override_settings(MFA_OBLIGATOIRE=False)
    def test_connexion_puis_accueil_sans_mfa(self):
        User.objects.create_user("gestion", password="mot-de-passe-solide")
        reponse = self.client.post(
            reverse("connexion"), {"username": "gestion", "password": "mot-de-passe-solide"}
        )
        self.assertRedirects(reponse, reverse("accueil"))
        self.assertContains(self.client.get(reverse("accueil")), "gestionnaire")


def code_courant(appareil):
    return f"{totp(appareil.bin_key, appareil.step, appareil.t0, appareil.digits):06d}"


class DoubleAuthentificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("gestion", password="mot-de-passe-solide")
        self.client.post(
            reverse("connexion"), {"username": "gestion", "password": "mot-de-passe-solide"}
        )

    def test_mot_de_passe_seul_ne_suffit_pas(self):
        self.assertRedirects(
            self.client.get(reverse("accueil")), f"{reverse('mfa')}?next=/", fetch_redirect_response=False
        )
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_premiere_connexion_enrole_un_appareil(self):
        reponse = self.client.get(reverse("mfa"))
        self.assertContains(reponse, "<svg")
        appareil = TOTPDevice.objects.get(user=self.user, confirmed=False)

        reponse = self.client.post(reverse("mfa"), {"code": code_courant(appareil), "next": "/"})
        self.assertRedirects(reponse, "/")
        appareil.refresh_from_db()
        self.assertTrue(appareil.confirmed)
        self.assertEqual(self.client.get(reverse("accueil")).status_code, 200)

    def test_code_faux_refuse(self):
        self.client.get(reverse("mfa"))
        reponse = self.client.post(reverse("mfa"), {"code": "000000"})
        self.assertContains(reponse, "Code incorrect")
        self.assertEqual(self.client.get(reverse("accueil")).status_code, 302)

    def test_appareil_confirme_pas_de_nouveau_qr(self):
        appareil = TOTPDevice.objects.create(user=self.user, confirmed=True)
        reponse = self.client.get(reverse("mfa"))
        self.assertNotContains(reponse, "<svg")
        reponse = self.client.post(reverse("mfa"), {"code": code_courant(appareil)})
        self.assertRedirects(reponse, "/")

    def test_redirection_externe_ignoree(self):
        appareil = TOTPDevice.objects.create(user=self.user, confirmed=True)
        reponse = self.client.post(
            reverse("mfa"), {"code": code_courant(appareil), "next": "https://exemple.com/"}
        )
        self.assertRedirects(reponse, "/")

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


class VerrouillageTests(TestCase):
    def test_identifiant_bloque_apres_cinq_echecs(self):
        User.objects.create_user("gestion", password="mot-de-passe-solide")
        for _ in range(5):
            self.client.post(reverse("connexion"), {"username": "gestion", "password": "faux"})
        reponse = self.client.post(
            reverse("connexion"), {"username": "gestion", "password": "mot-de-passe-solide"}
        )
        self.assertContains(reponse, "Connexion bloquée", status_code=429)
