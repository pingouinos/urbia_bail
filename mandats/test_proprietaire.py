import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.champs import iban_valide
from core.testing import creer_gestionnaire
from proprietes.models import Bailleur, Piece

from .models import Mandat
from .tests import Donnees

IBAN = "FR76 3000 6000 0112 3456 7890 189"


class IbanTests(TestCase):
    def test_cle_de_controle(self):
        iban_valide(IBAN)
        iban_valide("DE89370400440532013000")
        with self.assertRaises(ValidationError):
            iban_valide("FR76 3000 6000 0112 3456 7890 188")
        with self.assertRaises(ValidationError):
            iban_valide("123456")


class LienProprietaireTests(Donnees, TestCase):
    """Lien envoyé au propriétaire pour qu'il vérifie ses coordonnées, donne
    son IBAN et dépose ses pièces, en trois étapes."""

    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier)
        reglage = override_settings(MEDIA_ROOT=self.dossier)
        reglage.enable()
        self.addCleanup(reglage.disable)
        self.client.force_login(creer_gestionnaire("gestion", first_name="Elodie", last_name="Durand"))
        self.mandat = self.creer_mandat()
        bailleur = self.mandat.propriete.bailleur
        bailleur.email = "paul.durand@exemple.fr"
        bailleur.save()

    def creer_lien(self):
        reponse = self.client.post(reverse("mandats:lien", args=[self.mandat.pk]), {"action": "creer"})
        self.assertRedirects(reponse, reverse("mandats:lien", args=[self.mandat.pk]))
        self.mandat.refresh_from_db()
        self.client.logout()
        return reverse("formulaire_proprietaire", args=[self.mandat.jeton])

    def vous(self, **autres):
        donnees = {
            "civilite": "M.", "nom": "Durand", "prenom": "Paul", "conjoint_civilite": "Mme",
            "conjoint_nom": "Martin", "conjoint_prenom": "Anne", "adresse": "3 place du Capitole",
            "code_postal": "31000", "ville": "Toulouse", "telephone": "06 12 34 56 78",
            "email": "paul.durand@exemple.fr",
        }
        donnees.update(autres)
        return donnees

    def test_bouton_et_message(self):
        page = self.client.get(self.mandat.get_absolute_url())
        self.assertContains(page, "Envoyer le lien")
        formulaire = self.creer_lien()
        self.client.force_login(User.objects.get(username="gestion"))
        page = self.client.get(reverse("mandats:lien", args=[self.mandat.pk]))
        self.assertContains(page, f"http://testserver{formulaire}")
        self.assertContains(page, "paul.durand@exemple.fr")
        self.assertIn("Elodie Durand", page.context["form"].initial["texte"])
        self.assertContains(self.client.get(self.mandat.get_absolute_url()), "Renvoyer le lien")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_HOST="smtp.exemple.fr")
    def test_envoi_par_e_mail(self):
        self.creer_lien()
        self.client.force_login(User.objects.get(username="gestion"))
        self.client.post(reverse("mandats:lien", args=[self.mandat.pk]), {"action": "envoyer", "texte": "Bonjour"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["paul.durand@exemple.fr"])

    def test_trois_etapes(self):
        formulaire = self.creer_lien()
        page = self.client.get(formulaire)
        self.assertContains(page, "9 rue des Lilas")
        self.assertContains(page, "Enregistrer et continuer")

        reponse = self.client.post(formulaire, self.vous())
        self.assertRedirects(reponse, f"{formulaire}?etape=2")
        bailleur = Bailleur.objects.get()
        self.assertEqual((bailleur.adresse, bailleur.ville), ("3 place du Capitole", "Toulouse"))
        self.mandat.refresh_from_db()
        # Pas encore signé : le mandat suit la fiche.
        self.assertEqual(self.mandat.mandant_adresse, "3 place du Capitole")
        self.assertEqual(self.mandat.mandant_code_postal_ville, "31000 Toulouse")

        reponse = self.client.post(f"{formulaire}?etape=2", {"versement": "trimestriel", "iban": "fr7630006000011234567890189"})
        self.assertRedirects(reponse, f"{formulaire}?etape=3")
        bailleur.refresh_from_db()
        self.assertEqual((bailleur.versement, bailleur.iban), ("trimestriel", IBAN))

        page = self.client.get(f"{formulaire}?etape=3")
        self.assertContains(page, "identité bancaire")
        pdf = SimpleUploadedFile("rib.pdf", b"%PDF-1.7 rib", content_type="application/pdf")
        reponse = self.client.post(f"{formulaire}?etape=3", {"type": "rib", "fichier": pdf})
        self.assertRedirects(reponse, f"{formulaire}?etape=3#pieces", fetch_redirect_response=False)
        piece = Piece.objects.get()
        self.assertEqual((piece.type, piece.bailleur, piece.agence), ("rib", bailleur, self.mandat.agence))
        self.assertContains(self.client.get(f"{formulaire}?etape=3"), "✓ Reçue")

        taxe = SimpleUploadedFile("taxe.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, content_type="image/png")
        self.client.post(f"{formulaire}?etape=3", {"type": "taxe_fonciere", "fichier": taxe})
        self.assertEqual(Piece.objects.get(type="taxe_fonciere").propriete, self.mandat.propriete)

        reponse = self.client.post(f"{formulaire}?etape=3", {"action": "envoyer"})
        self.assertRedirects(reponse, reverse("formulaire_proprietaire_merci"))
        self.mandat.refresh_from_db()
        self.assertIsNotNone(self.mandat.rempli_le)
        # Le lien sert encore, pour revenir avec une pièce oubliée.
        self.assertEqual(self.client.get(formulaire).status_code, 200)

    def test_erreurs_signalees(self):
        formulaire = self.creer_lien()
        page = self.client.post(formulaire, self.vous(email="", code_postal=""))
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["form"].errors.keys() >= {"email", "code_postal"})
        page = self.client.post(f"{formulaire}?etape=2", {"versement": "mensuel", "iban": "FR76 1234"})
        self.assertIn("iban", page.context["form"].errors)
        faux = SimpleUploadedFile("rib.pdf", b"MZ pas un pdf", content_type="application/pdf")
        page = self.client.post(f"{formulaire}?etape=3", {"type": "rib", "fichier": faux})
        self.assertContains(page, "ni un PDF ni une image")
        self.assertFalse(Piece.objects.exists())

    def test_pas_de_piece_hors_du_dossier(self):
        formulaire = self.creer_lien()
        pdf = SimpleUploadedFile("x.pdf", b"%PDF-1.7", content_type="application/pdf")
        self.client.post(f"{formulaire}?etape=3", {"type": "amiante", "fichier": pdf})
        self.assertFalse(Piece.objects.exists())

    def test_mandat_signe_garde_ses_parties(self):
        self.mandat.date_signature = timezone.localdate()
        self.mandat.save()
        formulaire = self.creer_lien()
        self.client.post(formulaire, self.vous())
        self.mandat.refresh_from_db()
        self.assertEqual(self.mandat.mandant_adresse, "9 rue des Lilas")
        self.assertEqual(Bailleur.objects.get().adresse, "3 place du Capitole")

    def test_ne_touche_pas_aux_autres_champs(self):
        formulaire = self.creer_lien()
        self.client.post(formulaire, self.vous(type="morale", siren="123"))
        bailleur = Bailleur.objects.get()
        self.assertEqual((bailleur.type, bailleur.siren), ("physique", ""))

    def test_lien_expire_ou_remplace(self):
        formulaire = self.creer_lien()
        Mandat.objects.filter(pk=self.mandat.pk).update(lien_cree_le=timezone.now() - timedelta(days=31))
        self.assertContains(self.client.get(formulaire), "plus valable", status_code=404)
        self.assertEqual(self.client.get(reverse("formulaire_proprietaire", args=["inconnu"])).status_code, 404)

    def test_mandat_termine(self):
        formulaire = self.creer_lien()
        Mandat.objects.filter(pk=self.mandat.pk).update(date_fin=timezone.localdate())
        self.assertEqual(self.client.get(formulaire).status_code, 404)

    @override_settings(URL_LOCATAIRES="https://locataire.exemple.fr", HOTE_LOCATAIRES="locataire.exemple.fr",
                       ALLOWED_HOSTS=["testserver", "locataire.exemple.fr"])
    def test_ouvert_sur_l_adresse_publique(self):
        formulaire = self.creer_lien()
        self.assertEqual(self.client.get(formulaire, HTTP_HOST="locataire.exemple.fr").status_code, 200)
        self.assertEqual(self.client.get("/mandats/", HTTP_HOST="locataire.exemple.fr").status_code, 404)
