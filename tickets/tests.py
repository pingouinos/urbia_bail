import tempfile
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.agence import activer
from core.models import Organisation
from core.testing import creer_gestionnaire

from .models import Ticket
from .views import appareil, lien_issue

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
CHROME_ANDROID = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36"


@override_settings(MFA_OBLIGATOIRE=False, MEDIA_ROOT=tempfile.mkdtemp())
class TicketsTests(TestCase):
    """Un collaborateur ouvre un ticket depuis n'importe quelle page ;
    l'exploitant le suit dans la Plateforme, toutes agences confondues."""

    def setUp(self):
        self.urbia = Organisation.objects.create(nom="URBIA Immobilier")
        self.canal = Organisation.objects.create(nom="Agence du Canal")
        with activer(self.urbia):
            self.elodie = creer_gestionnaire("elodie", email="elodie@urbia.example", first_name="Elodie", last_name="Martin")
            self.collegue = creer_gestionnaire("paul")
        with activer(self.canal):
            self.lea = creer_gestionnaire("lea")
        self.exploitant = User.objects.create_superuser("exploitant", email="editeur@example.com", password="x")

    def ouvrir(self, client_user, texte="La page reste blanche sur « Rédiger le bail ».", **autres):
        self.client.force_login(client_user)
        donnees = {"type": Ticket.Type.BUG, "texte": texte, "page": "/candidatures/12/", **autres}
        return self.client.post(reverse("tickets:ouvrir"), donnees, HTTP_USER_AGENT=CHROME_ANDROID)

    def test_lien_dans_le_menu_avec_la_page_courante(self):
        self.client.force_login(self.elodie)
        page = self.client.get(reverse("contacts:liste"))
        self.assertContains(page, f'{reverse("tickets:ouvrir")}?page=/contacts/')
        formulaire = self.client.get(reverse("tickets:ouvrir"), {"page": "/contacts/"})
        self.assertContains(formulaire, "Page jointe : /contacts/")

    def test_ouvrir_un_ticket(self):
        reponse = self.ouvrir(self.elodie)
        # Retour à la page d'où le ticket est parti.
        self.assertRedirects(reponse, "/candidatures/12/", fetch_redirect_response=False)
        ticket = Ticket.tous.get()
        self.assertEqual((ticket.agence, ticket.auteur, ticket.etat), (self.urbia, self.elodie, Ticket.Etat.RECU))
        self.assertEqual(ticket.page, "/candidatures/12/")
        self.assertEqual(ticket.appareil, "Chrome · téléphone")
        # L'exploitant est prévenu par e-mail, avec le lien vers la Plateforme ;
        # répondre à l'e-mail écrit à l'auteur.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["editeur@example.com"])
        self.assertEqual(mail.outbox[0].reply_to, ["elodie@urbia.example"])
        self.assertIn("Elodie Martin (URBIA Immobilier)", mail.outbox[0].body)
        self.assertIn(reverse("plateforme_ticket", args=[ticket.pk]), mail.outbox[0].body)

    def test_sans_page_retour_au_tableau_de_bord(self):
        self.assertRedirects(self.ouvrir(self.elodie, page=""), reverse("accueil"), fetch_redirect_response=False)

    def test_page_exterieure_ignoree(self):
        for page in ("https://exemple.com/", "//exemple.com/", "/\\exemple.com/", "javascript:alert(1)"):
            with self.subTest(page=page):
                self.assertRedirects(self.ouvrir(self.elodie, page=page), reverse("accueil"), fetch_redirect_response=False)
                self.assertNotContains(self.client.get(reverse("tickets:ouvrir"), {"page": page}), "exemple.com")
        self.assertEqual(set(Ticket.tous.values_list("page", flat=True)), {""})

    def test_capture(self):
        self.ouvrir(self.elodie, capture=SimpleUploadedFile("ecran.png", PNG, content_type="image/png"))
        ticket = Ticket.tous.get()
        self.assertTrue(ticket.capture.name.startswith("tickets/"))
        # Seul l'exploitant l'ouvre.
        self.assertEqual(self.client.get(reverse("plateforme_ticket_capture", args=[ticket.pk])).status_code, 403)
        self.client.force_login(self.exploitant)
        self.assertEqual(
            self.client.get(reverse("plateforme_ticket_capture", args=[ticket.pk]))["Content-Type"], "image/png",
        )

    def test_capture_qui_n_est_pas_une_image(self):
        reponse = self.ouvrir(self.elodie, capture=SimpleUploadedFile("ecran.png", b"pas une image"))
        self.assertContains(reponse, "ni un PDF ni une image")
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(Ticket.tous.exists())

    def test_pas_de_suivi_cote_utilisateur(self):
        self.ouvrir(self.elodie, texte="Ticket d'Elodie")
        self.assertNotContains(self.client.get(reverse("tickets:ouvrir")), "Ticket d&#x27;Elodie")

    def test_reserve_a_l_exploitant(self):
        self.ouvrir(self.elodie)
        ticket = Ticket.tous.get()
        for page in (reverse("plateforme_tickets"), reverse("plateforme_ticket", args=[ticket.pk]),
                     reverse("plateforme_ticket_capture", args=[ticket.pk])):
            with self.subTest(page=page):
                self.assertEqual(self.client.get(page).status_code, 403)

    def test_suivi_par_l_exploitant(self):
        self.ouvrir(self.elodie, texte="Ticket d'Elodie")
        self.ouvrir(self.lea, texte="Ticket de Léa")
        ticket = Ticket.tous.get(auteur=self.elodie)
        self.client.force_login(self.exploitant)
        liste = self.client.get(reverse("plateforme_tickets"))
        self.assertContains(liste, "Ticket d&#x27;Elodie")
        self.assertContains(liste, "Ticket de Léa")
        self.assertContains(liste, "Agence du Canal")
        self.assertContains(liste, '<span class="compteur">2</span>', html=True)
        fiche = self.client.get(reverse("plateforme_ticket", args=[ticket.pk]))
        self.assertContains(fiche, "Préparer l'issue sur GitHub")
        self.assertContains(fiche, "mailto:elodie@urbia.example")
        reponse = self.client.post(reverse("plateforme_ticket", args=[ticket.pk]), {
            "etat": Ticket.Etat.EN_COURS, "issue": "https://github.com/pingouinos/urbia_bail/issues/200",
        })
        self.assertRedirects(reponse, reverse("plateforme_tickets"))
        ticket.refresh_from_db()
        self.assertEqual(ticket.etat, Ticket.Etat.EN_COURS)
        self.assertEqual(ticket.issue, "https://github.com/pingouinos/urbia_bail/issues/200")

    def test_resolus_masques_par_defaut(self):
        self.ouvrir(self.elodie, texte="Déjà réglé")
        Ticket.tous.update(etat=Ticket.Etat.RESOLU)
        self.client.force_login(self.exploitant)
        self.assertNotContains(self.client.get(reverse("plateforme_tickets")), "Déjà réglé")
        self.assertContains(self.client.get(reverse("plateforme_tickets"), {"etat": "tous"}), "Déjà réglé")

    def test_issue_preremplie_sans_nom_d_auteur(self):
        self.ouvrir(self.elodie)
        lien = urlparse(lien_issue(Ticket.tous.get()))
        self.assertEqual(lien.path, "/pingouinos/urbia_bail/issues/new")
        parametres = parse_qs(lien.query)
        self.assertIn("Rédiger le bail", parametres["body"][0])
        self.assertIn("/candidatures/12/", parametres["body"][0])
        self.assertNotIn("Elodie", parametres["body"][0] + parametres["title"][0])

    @override_settings(TICKETS_DEPOT_GITHUB="")
    def test_sans_depot_pas_de_bouton(self):
        self.ouvrir(self.elodie)
        self.assertEqual(lien_issue(Ticket.tous.get()), "")

    def test_appareil(self):
        self.assertEqual(appareil(CHROME_ANDROID), "Chrome · téléphone")
        self.assertEqual(appareil("Mozilla/5.0 (Windows NT 10.0) Gecko/20100101 Firefox/131.0"), "Firefox · ordinateur")
        self.assertEqual(appareil(""), "navigateur inconnu · ordinateur")
