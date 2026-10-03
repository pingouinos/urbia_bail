from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

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


class VerrouillageTests(TestCase):
    def test_identifiant_bloque_apres_cinq_echecs(self):
        User.objects.create_user("gestion", password="mot-de-passe-solide")
        for _ in range(5):
            self.client.post(reverse("connexion"), {"username": "gestion", "password": "faux"})
        reponse = self.client.post(
            reverse("connexion"), {"username": "gestion", "password": "mot-de-passe-solide"}
        )
        self.assertContains(reponse, "Connexion bloquée", status_code=429)


GOOGLE = {
    "GOOGLE_DOMAINE": "agence.fr",
    "GOOGLE_CONNEXION_ACTIVE": True,
    "OIDC_RP_CLIENT_ID": "client",
    "OIDC_RP_CLIENT_SECRET": "secret",
    "OIDC_AUTH_REQUEST_EXTRA_PARAMS": {"hd": "agence.fr"},
}


def claims(**autres):
    return {
        "email": "elodie@agence.fr",
        "email_verified": True,
        "hd": "agence.fr",
        "given_name": "Elodie",
        "family_name": "Martin",
        **autres,
    }


@override_settings(**GOOGLE)
class GoogleWorkspaceTests(TestCase):
    def connexion_google(self, infos):
        """Rejoue le parcours OIDC complet, Google étant simulé."""
        reponse = self.client.get(reverse("oidc_authentication_init"))
        etat = parse_qs(urlparse(reponse.url).query)["state"][0]
        self.assertIn("hd=agence.fr", reponse.url)
        with mock.patch.multiple(
            "comptes.google.GoogleWorkspaceBackend",
            get_token=mock.Mock(return_value={"id_token": "x", "access_token": "y"}),
            verify_token=mock.Mock(return_value={"sub": "1"}),
            get_userinfo=mock.Mock(return_value=infos),
        ):
            return self.client.get(reverse("oidc_authentication_callback"), {"code": "c", "state": etat})

    def test_compte_existant_connecte_sans_totp(self):
        user = User.objects.create_user("elodie", email="Elodie@agence.fr")
        reponse = self.connexion_google(claims())
        self.assertRedirects(reponse, "/", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("accueil")).status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.last_name, "Martin")

    def test_compte_inconnu_refuse_par_defaut(self):
        reponse = self.connexion_google(claims())
        self.assertEqual(reponse.url, "/connexion/?echec=google")
        self.assertFalse(User.objects.exists())

    @override_settings(OIDC_CREATE_USER=True)
    def test_creation_auto_en_gestionnaire(self):
        self.connexion_google(claims())
        user = User.objects.get(email="elodie@agence.fr")
        self.assertEqual(list(user.groups.values_list("name", flat=True)), [GESTIONNAIRES])
        self.assertFalse(user.has_usable_password())

    def test_autre_domaine_refuse(self):
        User.objects.create_user("x", email="elodie@gmail.com")
        reponse = self.connexion_google(claims(email="elodie@gmail.com", hd=None))
        self.assertEqual(reponse.url, "/connexion/?echec=google")

    def test_hd_falsifie_refuse(self):
        User.objects.create_user("x", email="elodie@gmail.com")
        reponse = self.connexion_google(claims(email="elodie@gmail.com"))
        self.assertEqual(reponse.url, "/connexion/?echec=google")

    def test_email_non_verifie_refuse(self):
        User.objects.create_user("elodie", email="elodie@agence.fr")
        reponse = self.connexion_google(claims(email_verified=False))
        self.assertEqual(reponse.url, "/connexion/?echec=google")

    def test_compte_desactive_perd_l_acces_en_cours_de_session(self):
        user = User.objects.create_user("elodie", email="elodie@agence.fr")
        self.connexion_google(claims())
        self.assertEqual(self.client.get(reverse("accueil")).status_code, 200)
        user.is_active = False
        user.save()
        self.assertEqual(self.client.get(reverse("accueil")).status_code, 302)

    def test_bouton_google_sur_la_page_de_connexion(self):
        self.assertContains(self.client.get(reverse("connexion")), "Se connecter avec Google")
