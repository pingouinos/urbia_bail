import io
from email.message import Message
from unittest import mock
from urllib.error import HTTPError, URLError

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from comptes.roles import ADMINISTRATEURS, attribuer_role
from core.testing import creer_gestionnaire
from contrats.tests import Donnees
from documents.models import Modele, VersionContrat

from .catalogue import SOURCES
from .collecte import ErreurCollecte, TAILLE_MAX, TextePrincipal, collecter, telecharger, verifier_url
from .models import Alerte, Decision, Instantane, Source

CLE = "bail-service-public"
URL = SOURCES[CLE].url


class ExtractionTests(SimpleTestCase):
    def test_texte_principal_seul_et_espaces_normalises(self):
        parser = TextePrincipal()
        parser.feed('<nav>menu</nav><main><h1>Bail</h1><p>' + 'Clause du bail. ' * 30
                    + '</p><script>secret</script><nav>navigation</nav></main><footer>pied</footer>')
        texte = parser.texte()
        self.assertTrue(texte.startswith("Bail\nClause du bail."))
        for parasite in ("menu", "secret", "navigation", "pied"):
            self.assertNotIn(parasite, texte)

    def test_page_inconnue_ou_vide_refusee(self):
        for html in ("<body>" + "Contenu " * 100 + "</body>", "<main>captcha</main>"):
            with self.subTest(html=html[:30]):
                parser = TextePrincipal()
                parser.feed(html)
                with self.assertRaises(ErreurCollecte):
                    parser.texte()

    def test_adresses_non_autorisees(self):
        for url in ("http://www.anil.org/", "https://127.0.0.1/", "https://www.anil.org.evil.test/",
                    "https://user:pass@www.anil.org/", "https://www.anil.org:8443/", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(ErreurCollecte):
                verifier_url(url)

    @mock.patch("veille.collecte.build_opener")
    def test_redirection_vers_reseau_interne_bloquee(self, construire):
        headers = Message()
        headers["Location"] = "http://169.254.169.254/latest/"
        construire.return_value.open.side_effect = HTTPError(URL, 302, "redirect", headers, io.BytesIO())
        with self.assertRaises(ErreurCollecte):
            telecharger(URL)
        self.assertEqual(construire.return_value.open.call_count, 1)

    def reponse(self, mime="text/html", contenu=b"x"):
        reponse = mock.MagicMock()
        reponse.__enter__.return_value = reponse
        reponse.status = 200
        reponse.headers = Message()
        reponse.headers["Content-Type"] = mime
        reponse.read.side_effect = io.BytesIO(contenu).read
        return reponse

    @mock.patch("veille.collecte.build_opener")
    def test_type_taille_encodage_et_timeout(self, construire):
        for reponse in (self.reponse("application/pdf"), self.reponse(contenu=b"x" * (TAILLE_MAX + 1)),
                        self.reponse(contenu=b"\xff")):
            construire.return_value.open.return_value = reponse
            with self.assertRaises(ErreurCollecte):
                telecharger(URL)
        construire.return_value.open.side_effect = TimeoutError("délai")
        with self.assertRaises(TimeoutError):
            telecharger(URL)
        self.assertEqual(construire.return_value.open.call_args.kwargs["timeout"], 15)

    @mock.patch("veille.collecte.build_opener")
    def test_reponse_valide_et_redirection_autorisee(self, construire):
        headers = Message()
        headers["Location"] = "/particuliers/vosdroits/F920/"
        contenu = ("<main><p>" + "Texte du bail. " * 30 + "</p></main>").encode()
        construire.return_value.open.side_effect = [HTTPError(URL, 301, "redirect", headers, io.BytesIO()),
                                                   self.reponse(contenu=contenu)]
        url, texte = telecharger(URL)
        self.assertEqual(url, URL + "/")
        self.assertIn("Texte du bail", texte)


class CollecteTests(TestCase):
    def collecter(self, texte):
        with mock.patch("veille.collecte.telecharger", return_value=(URL, texte)):
            return collecter(CLE)

    def test_baseline_puis_changement_dedoublonne_et_retour_ancienne_version(self):
        self.assertEqual(self.collecter("Version A"), "référence initiale")
        self.assertFalse(Alerte.objects.exists())
        self.assertEqual(self.collecter("Version A"), "inchangé")
        self.assertEqual(Instantane.objects.count(), 1)
        self.assertEqual(self.collecter("Version B"), "alerte créée")
        alerte = Alerte.objects.get()
        self.assertEqual(alerte.statut, Alerte.Statut.A_ANALYSER)
        self.assertIn("+Version B", alerte.differences)
        self.assertEqual(alerte.modeles_potentiels, ["bail_habitation.docx"])
        self.collecter("Version B")
        self.assertEqual(Alerte.objects.count(), 1)
        self.collecter("Version A")
        self.assertEqual(Alerte.objects.count(), 2)
        self.assertEqual(Instantane.objects.count(), 3)

    def test_echec_preserve_succes_et_reprise(self):
        self.collecter("Version A")
        succes = Source.objects.get().dernier_succes
        with mock.patch("veille.collecte.telecharger", side_effect=URLError("indisponible")):
            self.assertEqual(collecter(CLE), "erreur")
        source = Source.objects.get()
        self.assertEqual(source.dernier_succes, succes)
        self.assertIn("indisponible", source.erreur)
        self.assertGreaterEqual(source.dernier_essai, succes)
        self.assertEqual(Instantane.objects.count(), 1)
        self.collecter("Version A")
        self.assertEqual(Source.objects.get().erreur, "")
        self.assertFalse(Alerte.objects.exists())

    @mock.patch("veille.collecte.telecharger", side_effect=ErreurCollecte("structure inconnue"))
    def test_commande_signale_echec_mais_controle_les_autres_sources(self, telecharger_mock):
        with self.assertRaises(CommandError):
            call_command("collecter_veille", stdout=io.StringIO())
        self.assertEqual(telecharger_mock.call_count, len(SOURCES))
        self.assertEqual(Source.objects.filter(dernier_succes__isnull=True).count(), len(SOURCES))

    @mock.patch("veille.collecte.telecharger", return_value=(URL, "Version A"))
    def test_commande_source_unique(self, telecharger_mock):
        sortie = io.StringIO()
        call_command("collecter_veille", source=CLE, stdout=sortie)
        self.assertIn("référence initiale", sortie.getvalue())
        self.assertEqual(telecharger_mock.call_count, 1)


@override_settings(MFA_OBLIGATOIRE=False)
class ParcoursTests(Donnees, TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin-veille")
        attribuer_role(self.admin, ADMINISTRATEURS)
        self.gestionnaire = creer_gestionnaire("gestion-veille")
        with mock.patch("veille.collecte.telecharger", side_effect=[(URL, "Version A"), (URL, "Version B <script>alert(1)</script>")]):
            collecter(CLE)
            collecter(CLE)
        self.alerte = Alerte.objects.get()
        self.tableau = reverse("veille:tableau")
        self.detail = reverse("veille:detail", args=[self.alerte.pk])

    def test_droits_lecture_et_examen(self):
        for url in (self.tableau, self.detail):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.gestionnaire)
        for url in (self.tableau, self.detail):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(self.detail, {"statut": "traite", "commentaire": "x"}).status_code, 403)
        self.assertFalse(Decision.objects.exists())
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.tableau), "Changements à examiner")
        page = self.client.get(self.detail)
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(page, "<script>alert(1)</script>")

    def test_justification_obligatoire_et_historique(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.detail, {"statut": "traite"}).status_code, 200)
        self.assertFalse(Decision.objects.exists())
        for statut in ("confirme", "traite"):
            self.assertRedirects(self.client.post(self.detail, {
                "statut": statut, "commentaire": "Référence vérifiée, date d'effet à qualifier.",
            }), self.detail)
        self.alerte.refresh_from_db()
        self.assertEqual(self.alerte.statut, "traite")
        self.assertEqual(Decision.objects.count(), 2)
        self.assertEqual(Decision.objects.first().auteur, self.admin)
        self.assertContains(self.client.get(self.detail), "Référence vérifiée")

    def test_veille_et_decision_ne_modifient_pas_modeles_ni_archives_signees(self):
        modele = Modele.objects.create(nom="bail_habitation.docx", origine="depot")
        bail = self.creer_bail()
        bail.date_signature = timezone.localdate()
        bail.save()
        archive = VersionContrat.objects.create(
            bail=bail, nature="signature", auteur=self.admin, auteur_nom="Admin", motif="Signature",
            contexte={"loyer": "700"}, modele=b"modele", docx=b"contrat signe", empreinte="a" * 64,
            date_signature=bail.date_signature,
        )
        attendu_modele = Modele.objects.values().get(pk=modele.pk)
        attendu_bail = type(bail).objects.values().get(pk=bail.pk)
        attendu_archive = VersionContrat.objects.values().get(pk=archive.pk)
        with mock.patch("veille.collecte.telecharger", return_value=(URL, "Version C")):
            collecter(CLE)
        self.client.force_login(self.admin)
        self.client.post(self.detail, {"statut": "traite", "commentaire": "Contrôle humain consigné."})
        self.assertEqual(Modele.objects.values().get(pk=modele.pk), attendu_modele)
        self.assertEqual(type(bail).objects.values().get(pk=bail.pk), attendu_bail)
        self.assertEqual(VersionContrat.objects.values().get(pk=archive.pk), attendu_archive)
