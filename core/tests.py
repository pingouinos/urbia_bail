import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from comptes.roles import ADMINISTRATEURS, GESTIONNAIRES, attribuer_role, est_administrateur
from mandats.models import Mandat
from proprietes.models import Bailleur, Propriete

from .management.commands.migrate import aligner_applications
from .models import Organisation


class AlignementApplicationsTests(TestCase):
    """Une base créée sous les anciens noms d'application (baux, biens) est
    alignée avant les migrations ; une base neuve n'est pas touchée."""

    def test_base_deja_alignee(self):
        self.assertEqual(aligner_applications(connection), [])

    def test_anciens_noms(self):
        with connection.cursor() as curseur:
            curseur.execute("CREATE TABLE baux_essai (id integer)")
            curseur.execute("CREATE TABLE biens_essai (id integer)")
            for application in ("baux", "biens"):
                curseur.execute(
                    "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, CURRENT_TIMESTAMP)",
                    [application, "0001_initial"],
                )
            curseur.execute("INSERT INTO django_content_type (app_label, model) VALUES (%s, %s)", ["baux", "essai"])
        faits = aligner_applications(connection)
        self.assertEqual(sorted(faits), ["baux_essai → contrats_essai", "biens_essai → proprietes_essai"])
        with connection.cursor() as curseur:
            tables = connection.introspection.table_names(curseur)
            curseur.execute("SELECT app FROM django_migrations WHERE name = %s AND app IN (%s, %s, %s, %s)",
                            ["0001_initial", "baux", "biens", "contrats", "proprietes"])
            applications = {ligne[0] for ligne in curseur.fetchall()}
            curseur.execute("SELECT app_label FROM django_content_type WHERE model = %s", ["essai"])
            self.assertEqual(curseur.fetchone()[0], "contrats")
        self.assertIn("contrats_essai", tables)
        self.assertIn("proprietes_essai", tables)
        self.assertNotIn("baux_essai", tables)
        self.assertEqual(applications, {"contrats", "proprietes"})
        # Rejouer l'alignement ne fait plus rien.
        self.assertEqual(aligner_applications(connection), [])


@override_settings(MFA_OBLIGATOIRE=False)
class OrganisationTests(TestCase):
    """La fiche de l'agence alimente les documents et les messages ; seuls les
    administrateurs la modifient."""

    DONNEES = {
        "nom": "Agence du Canal", "raison_sociale": "CANAL", "forme_juridique": "SARL", "capital": "8000",
        "siren": "732 829 320", "rcs_ville": "Toulouse", "adresse": "1 quai de Brienne", "code_postal": "31000",
        "ville": "Toulouse", "telephone": "05 00 00 00 00", "email": "contact@canal.example", "site": "",
        "carte_pro": "CPI 3101 2020 000 000 001", "carte_delivree_par": "la CCI de Toulouse",
        "garant": "GARANTIE SA", "garant_adresse": "2 rue de la Paix, 75002 Paris", "garantie_montant": "120000",
        "taux_gestion": "6.5",
    }

    def connecter(self, role):
        utilisateur = User.objects.create_user(role.lower(), password="x")
        attribuer_role(utilisateur, role)
        self.client.force_login(utilisateur)
        return utilisateur

    def test_fiche_vide_puis_remplie(self):
        self.assertEqual(Organisation.objects.count(), 0)
        organisation = Organisation.courante()
        self.assertEqual(organisation.designation, "Urbia Gestion")
        self.assertIn("carte professionnelle", organisation.manquants)
        self.connecter(ADMINISTRATEURS)
        self.assertContains(self.client.get(reverse("accueil")), "La fiche de l'agence est incomplète")
        reponse = self.client.post(reverse("agence"), self.DONNEES)
        self.assertRedirects(reponse, reverse("agence"))
        organisation = Organisation.courante()
        self.assertEqual(Organisation.objects.count(), 1)
        self.assertEqual(organisation.manquants, [])
        self.assertEqual(organisation.contexte_document()["garantie_montant"], "120 000")
        self.assertNotContains(self.client.get(reverse("accueil")), "La fiche de l'agence est incomplète")

    def test_mentions_obligatoires(self):
        self.connecter(ADMINISTRATEURS)
        reponse = self.client.post(reverse("agence"), {**self.DONNEES, "carte_pro": ""})
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(Organisation.courante().nom)

    def test_reservee_aux_administrateurs(self):
        self.connecter(GESTIONNAIRES)
        self.assertEqual(self.client.get(reverse("agence")).status_code, 403)
        self.assertEqual(self.client.post(reverse("agence"), self.DONNEES).status_code, 403)
        self.assertNotContains(self.client.get(reverse("accueil")), reverse("agence"))

    def test_logo(self):
        self.connecter(ADMINISTRATEURS)
        with tempfile.TemporaryDirectory() as dossier, self.settings(MEDIA_ROOT=dossier):
            self.assertEqual(self.client.get(reverse("logo_agence")).status_code, 404)
            faux = SimpleUploadedFile("logo.png", b"<svg onload=alert(1)>", content_type="image/png")
            reponse = self.client.post(reverse("agence"), {**self.DONNEES, "logo": faux})
            self.assertContains(reponse, "Fournir une image PNG, JPEG ou WebP.")
            image = SimpleUploadedFile("logo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, content_type="image/png")
            self.client.post(reverse("agence"), {**self.DONNEES, "logo": image})
            self.client.logout()
            # Le logo figure sur le formulaire du candidat, ouvert sans connexion.
            reponse = self.client.get(reverse("logo_agence"))
            self.assertEqual(reponse.status_code, 200)
            self.assertEqual(reponse["Content-Type"], "image/png")
            # Lu sans fermer la réponse : sa fermeture couperait la connexion à la base du test.
            self.assertTrue(reponse.getvalue().startswith(b"\x89PNG"))

    def test_taux_propose_sur_un_nouveau_mandat(self):
        Organisation.objects.create(taux_gestion="6.5")
        bailleur = Bailleur.objects.create(nom="DURAND", prenom="Paul")
        propriete = Propriete.objects.create(bailleur=bailleur, adresse="3 rue des Lois", code_postal="31000", ville="Toulouse")
        self.assertEqual(str(Mandat.depuis_propriete(propriete).taux_gestion), "6.50")

    def test_aide_a_la_saisie_des_adresses(self):
        """Le script qui interroge les services d'adresses n'est servi qu'aux
        collaborateurs connectés, jamais sur les pages ouvertes au public."""
        self.assertNotContains(self.client.get(reverse("connexion")), "js/adresse.js")
        # Le choix de l'indicatif téléphonique n'appelle aucun service : il est servi partout.
        self.assertContains(self.client.get(reverse("connexion")), "js/telephone.js")
        self.connecter(ADMINISTRATEURS)
        self.assertContains(self.client.get(reverse("agence")), "js/adresse.js")

    def test_administration_dans_l_application(self):
        """Les administrateurs gèrent l'agence et les comptes depuis
        l'application ; un gestionnaire n'y a pas accès."""
        from django_otp.plugins.otp_totp.models import TOTPDevice

        pages = [reverse("administration"), reverse("utilisateurs"), reverse("utilisateur_creer")]
        gestionnaire = self.connecter(GESTIONNAIRES)
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 403)
        self.assertNotContains(self.client.get(reverse("accueil")), reverse("administration"))
        admin = self.connecter(ADMINISTRATEURS)
        accueil = self.client.get(reverse("accueil"))
        self.assertContains(accueil, reverse("administration"))
        self.assertNotContains(accueil, reverse("admin:index"))
        for page in pages:
            self.assertEqual(self.client.get(page).status_code, 200)
        self.assertContains(self.client.get(reverse("administration")), reverse("agence"))
        # Création d'un compte : l'adresse sert d'identifiant.
        donnees = {"first_name": "Lina", "last_name": "Roux", "email": "Lina@Agence.fr", "is_active": "on",
                   "role": GESTIONNAIRES, "mot_de_passe": "Un-mot-de-passe-solide-42"}
        self.assertRedirects(self.client.post(reverse("utilisateur_creer"), donnees), reverse("utilisateurs"))
        lina = User.objects.get(username="lina@agence.fr")
        self.assertTrue(lina.check_password("Un-mot-de-passe-solide-42"))
        self.assertFalse(est_administrateur(lina))
        reponse = self.client.post(reverse("utilisateur_creer"), donnees)
        self.assertContains(reponse, "Un compte existe déjà avec cette adresse.")
        # Promotion, puis désactivation ; le mot de passe laissé vide ne change pas.
        modifier = reverse("utilisateur_modifier", args=[lina.pk])
        self.client.post(modifier, {**donnees, "role": ADMINISTRATEURS, "mot_de_passe": ""})
        lina.refresh_from_db()
        self.assertTrue(est_administrateur(lina) and lina.check_password("Un-mot-de-passe-solide-42"))
        self.client.post(modifier, {**donnees, "role": GESTIONNAIRES, "mot_de_passe": "", "is_active": ""})
        lina.refresh_from_db()
        self.assertFalse(lina.is_active or est_administrateur(lina))
        # On ne se retire pas ses propres droits, et il reste toujours un administrateur.
        moi = reverse("utilisateur_modifier", args=[admin.pk])
        reponse = self.client.post(moi, {"first_name": "A", "last_name": "B", "email": "a@agence.fr", "is_active": "on", "role": GESTIONNAIRES})
        self.assertContains(reponse, "Vous ne pouvez pas retirer vos propres droits")
        self.assertTrue(est_administrateur(User.objects.get(pk=admin.pk)))
        # Dépannage : la double authentification se réinitialise.
        TOTPDevice.objects.create(user=gestionnaire, name="téléphone", confirmed=True)
        reponse = self.client.post(reverse("utilisateur_mfa", args=[gestionnaire.pk]), follow=True)
        self.assertContains(reponse, "Double authentification de")
        self.assertFalse(TOTPDevice.objects.filter(user=gestionnaire).exists())
        self.assertEqual(self.client.post(reverse("utilisateur_debloquer", args=[gestionnaire.pk])).status_code, 302)
        self.assertEqual(self.client.get(reverse("utilisateur_mfa", args=[gestionnaire.pk])).status_code, 405)
