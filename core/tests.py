import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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
        accueil = self.client.get(reverse("accueil"))
        self.assertNotContains(accueil, reverse("administration"))
        # Le rôle se lit sous le nom, dans le panneau.
        self.assertContains(accueil, '<span class="compte-role">Gestionnaire</span>')
        admin = self.connecter(ADMINISTRATEURS)
        accueil = self.client.get(reverse("accueil"))
        self.assertContains(accueil, reverse("administration"))
        self.assertNotContains(accueil, reverse("admin:index"))
        self.assertContains(accueil, '<span class="compte-role">Administrateur</span>')
        # Le tableau de bord ne porte plus d'action d'administration : une seule entrée, dans le panneau.
        self.assertEqual(accueil.content.decode().count(f'href="{reverse("administration")}"'), 1)
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

    def test_taches_du_tableau_de_bord(self):
        """Le tableau de bord liste les actions en attente, déduites des
        fiches ; une action faite fait disparaître sa ligne."""
        import datetime as dt

        from candidatures.models import Candidat, Candidature
        from contrats.models import Bail
        from proprietes.models import Piece

        from .taches import taches

        self.connecter(GESTIONNAIRES)
        self.assertContains(self.client.get(reverse("accueil")), "Rien en attente.")
        sci = Bailleur.objects.create(type="sci_familiale", nom="SCI ESGT")
        propriete = Propriete.objects.create(
            bailleur=sci, adresse="3 rue des Lois", code_postal="31000", ville="Toulouse", dernier_loyer=600,
        )
        mandat = Mandat.depuis_propriete(propriete)
        mandat.save()
        refusee = Candidature.objects.create(propriete=propriete, statut="non_retenue")
        Candidat.objects.create(candidature=refusee, nom="Roux", prenom="Lina")
        Piece.objects.create(propriete=propriete, type="dpe", fichier="proprietes/dpe.pdf", date_document=dt.date(2019, 1, 1))
        bail = Bail.depuis_propriete(propriete)
        bail.date_effet, bail.date_signature = dt.date(2024, 1, 1), dt.date(2023, 12, 20)
        bail.conge_recu_le, bail.date_depart_prevue = dt.date(2026, 1, 1), dt.date(2026, 4, 1)
        bail.irl_trimestre, bail.irl_valeur = "2e trimestre 2023", "140.59"
        bail.save()
        titres = {tache.titre: tache for tache in taches([])}
        for attendu in ("Réponse à envoyer à Lina ROUX", "Représentant de la société à renseigner",
                        "Diagnostic de performance énergétique (DPE) périmé", "Départ à confirmer : locataire"):
            self.assertIn(attendu, titres)
        # Une seule ligne par mandat : la signature et les pièces qui lui manquent.
        du_mandat = [titre for titre in titres if "Mandat" in titre or "dossier" in titre]
        self.assertEqual(len(du_mandat), 1)
        self.assertRegex(du_mandat[0], r"^Mandat à faire signer, \d+ pièces du dossier à fournir$")
        self.assertTrue(titres["Départ à confirmer : locataire"].urgent)
        page = self.client.get(reverse("accueil"))
        self.assertContains(page, "Mandat à faire signer")
        self.assertContains(page, "en retard")
        # Le mandat signé, sa ligne disparaît.
        mandat.date_signature = dt.date(2026, 9, 1)
        mandat.save()
        du_mandat = [tache.titre for tache in taches([]) if "dossier" in tache.titre]
        self.assertRegex(du_mandat[0], r"^\d+ pièces du dossier à fournir$")
        # Une propriété hors mandat est comptée en une seule ligne.
        Propriete.objects.create(bailleur=sci, adresse="5 rue du Taur", code_postal="31000", ville="Toulouse")
        self.assertIn("1 propriété sans mandat en cours", {tache.titre for tache in taches([])})

    def test_entetes_de_securite(self):
        reponse = self.client.get(reverse("connexion"))
        self.assertIn("frame-ancestors 'none'", reponse["Content-Security-Policy"])
        self.assertIn("https://data.geopf.fr", reponse["Content-Security-Policy"])
        self.assertIn("camera=()", reponse["Permissions-Policy"])
        self.assertIn("no-store", reponse["Cache-Control"])
        # Le parcours du candidat ne laisse fuir son lien personnel ni par le Referer ni par un cache.
        public = self.client.get("/locataire/jeton-inconnu/")
        self.assertEqual(public["Referrer-Policy"], "no-referrer")

    def test_adresse_du_client(self):
        from django.test import RequestFactory

        from .journal import adresse_ip

        demande = RequestFactory().get
        # Par le tunnel Cloudflare : cloudflared parle depuis la machine elle-même.
        self.assertEqual(adresse_ip(demande("/", HTTP_CF_CONNECTING_IP="203.0.113.7")), "203.0.113.7")
        # Un poste du réseau ne peut pas se faire passer pour un autre.
        self.assertEqual(
            adresse_ip(demande("/", REMOTE_ADDR="192.168.1.20", HTTP_CF_CONNECTING_IP="203.0.113.7")), "192.168.1.20",
        )
        with self.settings(HTTPS=True):
            self.assertEqual(
                adresse_ip(demande("/", REMOTE_ADDR="172.18.0.2", HTTP_X_FORWARDED_FOR="1.2.3.4, 198.51.100.9")),
                "198.51.100.9",
            )

    def test_limite_de_debit_par_candidat_derriere_le_tunnel(self):
        from django.core.cache import cache

        cache.clear()
        with self.settings(LIMITE_FORMULAIRE_PUBLIC=2):
            premier = [self.client.get("/locataire/x/", HTTP_CF_CONNECTING_IP="203.0.113.7").status_code
                       for _ in range(3)]
            self.assertEqual(premier[-1], 429)
            # Un autre candidat, arrivé par le même tunnel, garde sa propre limite.
            self.assertNotEqual(self.client.get("/locataire/x/", HTTP_CF_CONNECTING_IP="203.0.113.8").status_code, 429)
        cache.clear()

    def test_limite_de_debit_du_formulaire_public(self):
        from django.core.cache import cache

        cache.clear()
        with self.settings(LIMITE_FORMULAIRE_PUBLIC=3):
            codes = [self.client.get("/locataire/jeton-inconnu/").status_code for _ in range(5)]
            self.assertEqual(codes[3:], [429, 429])
            self.assertNotIn(429, codes[:3])
            # Les pages internes ne sont pas concernées.
            self.assertEqual(self.client.get(reverse("connexion")).status_code, 200)
        cache.clear()

    def test_journal_de_securite(self):
        from .models import Evenement

        self.client.post(reverse("connexion"), {"username": "inconnu", "password": "faux"})
        self.assertTrue(Evenement.objects.filter(action="connexion refusée", identifiant="inconnu").exists())
        admin = self.connecter(ADMINISTRATEURS)
        self.assertTrue(Evenement.objects.filter(action="connexion", identifiant=admin.username).exists())
        self.client.post(reverse("utilisateur_creer"), {
            "first_name": "Lina", "last_name": "Roux", "email": "lina@agence.fr", "is_active": "on", "role": GESTIONNAIRES,
        })
        cree = Evenement.objects.get(action="compte créé")
        self.assertEqual((cree.objet, cree.utilisateur), ("lina@agence.fr", admin))
        self.client.get(reverse("proprietes:exporter"))
        self.assertTrue(Evenement.objects.filter(action="export", objet="Propriétés").exists())
        page = self.client.get(reverse("journal"))
        self.assertContains(page, "Compte créé")
        self.assertContains(self.client.get(reverse("journal"), {"action": "export"}), "1 événement(s)")
        # Réservé aux administrateurs.
        self.connecter(GESTIONNAIRES)
        self.assertEqual(self.client.get(reverse("journal")).status_code, 403)


@override_settings(MFA_OBLIGATOIRE=False)
class ProcessusTests(TestCase):
    """Mandat, bail et mise en location suivent des étapes déduites des
    fiches : la frise avance d'elle-même quand l'action est faite."""

    def setUp(self):
        from contrats.tests import Donnees

        self.donnees = Donnees()
        self.propriete = self.donnees.creer_propriete()

    def etats(self, processus):
        return [etape.etat for etape in processus.etapes]

    def test_mandat(self):
        import datetime as dt

        from .processus import processus_mandat

        mandat = Mandat.depuis_propriete(self.propriete)
        mandat.save()
        processus = processus_mandat(mandat)
        self.assertEqual(processus.courante.libelle, "Signature")
        self.assertEqual(self.etats(processus), ["faite", "courante", "a_venir", "a_venir", "a_venir"])
        self.assertIn("pièces à fournir", processus.etapes[2].detail)
        mandat.date_signature = dt.date(2026, 10, 1)
        processus = processus_mandat(mandat)
        self.assertEqual(processus.courante.libelle, "Dossier du mandant")
        self.assertEqual(processus_mandat(mandat, manquantes=[]).courante.libelle, "Gestion")
        # Terminé sans dossier complet : l'étape n'a plus à être relancée.
        mandat.date_fin = dt.date(2026, 12, 1)
        processus = processus_mandat(mandat)
        self.assertIsNone(processus.courante)
        self.assertEqual(processus.etapes[2].etat, "sautee")
        self.assertEqual(processus.avancement, 100)

    def test_bail(self):
        import datetime as dt
        from unittest import mock

        from .processus import processus_bail

        bail = self.donnees.creer_bail(propriete=self.propriete, date_effet=dt.date(2026, 11, 1))
        aujourd_hui = dt.date(2026, 10, 12)
        with mock.patch("django.utils.timezone.localdate", return_value=aujourd_hui):
            self.assertEqual(processus_bail(bail, alertes=["DPE manquant"]).courante.libelle, "Rédaction")
            self.assertEqual(processus_bail(bail, alertes=[]).courante.libelle, "Signature")
            bail.date_signature = dt.date(2026, 10, 10)
            processus = processus_bail(bail)
            self.assertEqual(processus.courante.libelle, "Entrée dans les lieux")
            self.assertTrue(processus.courante.prevue)
        with mock.patch("django.utils.timezone.localdate", return_value=dt.date(2026, 12, 1)):
            self.assertEqual(processus_bail(bail).courante.libelle, "Location en cours")
            bail.conge_recu_le, bail.date_depart_prevue = dt.date(2027, 1, 5), dt.date(2027, 4, 5)
        with mock.patch("django.utils.timezone.localdate", return_value=dt.date(2027, 2, 1)):
            self.assertEqual(processus_bail(bail).courante.libelle, "Préavis")
        with mock.patch("django.utils.timezone.localdate", return_value=dt.date(2027, 5, 1)):
            # Départ prévu passé et non confirmé : la sortie est en retard.
            processus = processus_bail(bail)
            self.assertEqual((processus.courante.libelle, processus.courante.etat), ("Sortie", "alerte"))
            bail.date_fin_effective = dt.date(2027, 4, 5)
            self.assertIsNone(processus_bail(bail).courante)

    def test_mise_en_location_sans_mandat(self):
        from .processus import processus_location

        self.propriete.mise_en_location_le = self.propriete.disponible_le = timezone.localdate()
        processus = processus_location(self.propriete, a_etudier=2)
        self.assertEqual(processus.courante.libelle, "Candidatures")
        self.assertEqual(processus.etapes[2].detail, "2 à étudier")
        # Le mandat manque alors que la location avance : il est signalé.
        self.assertEqual(processus.etapes[0].etat, "alerte")
        self.assertTrue(processus.en_alerte)

    def test_frises_affichees(self):
        attribuer_role(utilisateur := User.objects.create_user("elodie", password="x"), ADMINISTRATEURS)
        self.client.force_login(utilisateur)
        mandat = Mandat.depuis_propriete(self.propriete)
        mandat.save()
        bail = self.donnees.creer_bail(propriete=self.propriete)
        accueil = self.client.get(reverse("accueil"))
        self.assertContains(accueil, "Où en sont les dossiers")
        # Le mandat à faire signer et le bail à faire signer, chacun avec sa frise.
        self.assertContains(accueil, "Dossiers à faire avancer")
        self.assertContains(accueil, 'aria-label="Mandat : étapes"')
        self.assertContains(accueil, 'aria-label="Mise en location : étapes"')
        mises_en_location = dict(accueil.context["ensemble"][0][1])
        self.assertEqual(mises_en_location["Signature"], 1)
        page = self.client.get(bail.get_absolute_url())
        self.assertContains(page, "Avancement du bail")
        self.assertContains(page, 'aria-current="step"')
        self.assertContains(self.client.get(mandat.get_absolute_url()), "Avancement du mandat")
        self.assertContains(self.client.get(self.propriete.get_absolute_url()), 'aria-label="Mise en location : étapes"')
        # Logement loué : la fiche montre la frise du bail en cours.
        import datetime as dt

        loue = self.donnees.creer_propriete(adresse="3 rue du Taur")
        self.donnees.creer_bail(propriete=loue, date_effet=dt.date(2025, 1, 1), date_signature=dt.date(2024, 12, 20))
        self.assertContains(self.client.get(loue.get_absolute_url()), 'aria-label="Bail : étapes"')
