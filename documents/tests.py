import io
import shutil
import tempfile
import urllib.error
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document

from comptes.roles import ADMINISTRATEURS, attribuer_role
from mandats.tests import Donnees, texte_docx

from . import google
from .generation import MODELES_PAR_DEFAUT
from .models import Modele

LIEN = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit?usp=sharing"


def modele_mandat(*paragraphes):
    """Modèle de mandat d'origine augmenté de quelques paragraphes."""
    document = Document(MODELES_PAR_DEFAUT / "mandat_gestion.docx")
    for texte in paragraphes:
        document.add_paragraph(texte)
    flux = io.BytesIO()
    document.save(flux)
    return flux.getvalue()


class ReponseHttp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class GoogleTests(TestCase):
    def test_identifiant(self):
        self.assertEqual(google.identifiant(LIEN), "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        self.assertEqual(google.identifiant("https://docs.google.com/document/u/0/d/1AbCdEfGhIjKlMnOpQrStUvWx/edit"),
                         "1AbCdEfGhIjKlMnOpQrStUvWx")
        for lien in ["https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWx/view",
                     "https://docs.google.com.exemple.fr/document/d/1AbCdEfGhIjKlMnOpQrStUvWx", "", None]:
            with self.subTest(lien=lien):
                self.assertIsNone(google.identifiant(lien))

    def test_telechargement(self):
        with mock.patch("urllib.request.urlopen", return_value=ReponseHttp(b"PK\x03\x04contenu")) as appel:
            self.assertEqual(google.telecharger(LIEN), b"PK\x03\x04contenu")
        self.assertEqual(appel.call_args.args[0].full_url,
                         "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/export?format=docx")
        # Document non partagé : Google renvoie sa page de connexion.
        with mock.patch("urllib.request.urlopen", return_value=ReponseHttp(b"<!doctype html>")), \
                self.assertRaisesMessage(google.LectureImpossible, "n'est pas partagé"):
            google.telecharger(LIEN)
        erreur = urllib.error.HTTPError(LIEN, 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=erreur), \
                self.assertRaisesMessage(google.LectureImpossible, "Google refuse l'accès"):
            google.telecharger(LIEN)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("hors ligne")), \
                self.assertRaisesMessage(google.LectureImpossible, "injoignable"):
            google.telecharger(LIEN)


@override_settings(MFA_OBLIGATOIRE=False)
class ModelesTests(Donnees, TestCase):
    def setUp(self):
        dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dossier)
        reglage = override_settings(MEDIA_ROOT=dossier)
        reglage.enable()
        self.addCleanup(reglage.disable)
        admin = User.objects.create_user("elodie", first_name="Elodie")
        attribuer_role(admin, ADMINISTRATEURS)
        self.client.force_login(admin)
        self.mandat = self.creer_mandat()
        self.url = reverse("documents:reprendre", args=["mandat_gestion.docx"])

    def reprendre(self, contenu, lien=LIEN):
        with mock.patch("documents.google.telecharger", return_value=contenu):
            return self.client.post(self.url, {"lien_google": lien}, follow=True)

    def texte_mandat(self):
        return texte_docx(self.client.get(reverse("mandats:word", args=[self.mandat.pk])).content)

    def test_reprise_depuis_google_docs(self):
        reponse = self.reprendre(modele_mandat("Clause ajoutée dans Google Docs pour {{ mandant }}."))
        self.assertContains(reponse, "repris de Google Docs et mis en service")
        self.assertContains(reponse, "Repris de Google Docs, le")
        self.assertContains(reponse, f'value="{LIEN}"')
        self.assertIn("Clause ajoutée dans Google Docs pour Monsieur DURAND Paul", self.texte_mandat())
        modele = Modele.objects.get(nom="mandat_gestion.docx")
        self.assertEqual((modele.origine, modele.lien_google), (Modele.Origine.GOOGLE, LIEN))
        # Le lien est gardé : un second clic suffit pour la version suivante.
        self.reprendre(modele_mandat("Deuxième version."), lien=LIEN)
        self.assertIn("Deuxième version.", self.texte_mandat())
        # Retour à la version précédente, puis au modèle d'origine.
        self.client.post(reverse("documents:precedent", args=["mandat_gestion.docx"]))
        self.assertIn("Clause ajoutée dans Google Docs", self.texte_mandat())
        self.client.post(reverse("documents:retablir", args=["mandat_gestion.docx"]))
        self.assertNotIn("Clause ajoutée", self.texte_mandat())

    def test_modele_refuse(self):
        for paragraphe, message in [
            ("{% if mandant %}sans fin", "Une balise est mal écrite"),
            ("{{ balise_inconnue }}", "Balises inconnues : balise_inconnue"),
            ("{{ mandant.upper(1, 2, 3) }}", "Le remplissage d&#x27;essai échoue"),
        ]:
            with self.subTest(paragraphe=paragraphe):
                self.assertContains(self.reprendre(modele_mandat(paragraphe)), message)
                self.assertNotIn("sans fin", self.texte_mandat())
        self.assertFalse(Modele.objects.filter(origine=Modele.Origine.GOOGLE).exists())

    def test_lien_ou_partage_incorrect(self):
        reponse = self.client.post(self.url, {"lien_google": "https://exemple.fr/doc"}, follow=True)
        self.assertContains(reponse, "Coller le lien du Google Doc")
        with mock.patch("documents.google.telecharger", side_effect=google.LectureImpossible("Pas partagé.")):
            reponse = self.client.post(self.url, {"lien_google": LIEN}, follow=True)
        self.assertContains(reponse, "n&#x27;a pas été repris. Pas partagé.")
        # Le lien est retenu pour réessayer une fois le partage corrigé.
        self.assertEqual(Modele.objects.get(nom="mandat_gestion.docx").lien_google, LIEN)

    def test_document_essai(self):
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
            reponse = self.client.get(reverse("documents:essai", args=["mandat_gestion.docx"]))
        self.assertEqual(reponse.content, b"%PDF-1.7")
        reponse = self.client.get(reverse("documents:essai", args=["bail_habitation.docx"]), follow=True)
        self.assertContains(reponse, "Aucun bail d&#x27;habitation (nu ou meublé) enregistré")

    def test_reserve_aux_administrateurs(self):
        self.client.force_login(User.objects.create_user("gestion"))
        self.assertEqual(self.reprendre(modele_mandat()).status_code, 403)
        self.assertEqual(self.client.get(reverse("documents:essai", args=["mandat_gestion.docx"])).status_code, 403)
