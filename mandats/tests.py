import io
import shutil
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document
from openpyxl import load_workbook

from biens.models import Bailleur, Bien
from comptes.roles import ADMINISTRATEURS, attribuer_role
from documents import generation
from documents.generation import ConversionImpossible

from .models import Mandat


def texte_docx(contenu):
    return "\n".join(p.text for p in Document(io.BytesIO(contenu)).paragraphs)


class Donnees:
    def creer_bien(self, **autres):
        bailleur = Bailleur.objects.create(
            civilite="M.", nom="Durand", prenom="Paul", conjoint_civilite="Mme",
            conjoint_nom="Martin", conjoint_prenom="Anne",
            adresse="9 rue des Lilas", code_postal="31270", ville="Frouzins",
        )
        valeurs = {
            "bailleur": bailleur, "adresse": "12 avenue de Muret", "code_postal": "31300",
            "ville": "Toulouse", "surface": Decimal("39"), "nb_pieces": 2, "meuble": True,
            "zone_tendue": True, "dernier_loyer": Decimal("854"), "charges": Decimal("45"),
        }
        valeurs.update(autres)
        return Bien.objects.create(**valeurs)

    def creer_mandat(self, bien=None, **autres):
        mandat = Mandat.depuis_bien(bien or self.creer_bien())
        for champ, valeur in autres.items():
            setattr(mandat, champ, valeur)
        mandat.save()
        return mandat


class MandatTests(Donnees, TestCase):
    def test_numeros_du_registre_se_suivent(self):
        bien = self.creer_bien()
        numeros = [self.creer_mandat(bien).numero for _ in range(3)]
        self.assertEqual(numeros, [1, 2, 3])

    def test_prerempli_depuis_les_fiches(self):
        mandat = Mandat.depuis_bien(self.creer_bien())
        self.assertEqual(mandat.mandant, "Monsieur DURAND Paul et Madame MARTIN Anne")
        self.assertEqual(mandat.mandant_code_postal_ville, "31270 Frouzins")
        self.assertEqual(mandat.designation, "Appartement T2 meublé de 39 m²")
        self.assertEqual(mandat.adresse_bien, "12 avenue de Muret 31300 TOULOUSE")
        self.assertEqual((mandat.loyer_cc, mandat.charges), (Decimal("899"), Decimal("45")))

    def test_designation_personne_morale(self):
        self.assertEqual(Bailleur(type=Bailleur.Type.SCI_FAMILIALE, nom="SCI Les Pins").designation, "SCI Les Pins")

    def test_duree(self):
        self.assertEqual(Mandat(duree_ans=3).duree_texte, "une durée de trois ans")
        self.assertEqual(
            Mandat(duree_ans=1, reconductions=3).duree_texte,
            "une durée d’un an, le contrat se renouvellera par tacite reconduction à trois reprises",
        )

    def test_honoraires_conformes(self):
        mandat = self.creer_mandat(
            honoraires_bail_bailleur=390, honoraires_bail_locataire=390,
            honoraires_edl_bailleur=117, honoraires_edl_locataire=117,
        )
        self.assertEqual(mandat.alertes, [])

    def test_part_locataire_superieure_a_celle_du_bailleur(self):
        mandat = self.creer_mandat(honoraires_bail_locataire=390, honoraires_edl_locataire=117)
        self.assertEqual(len(mandat.alertes), 2)
        self.assertIn("dépasse celle du bailleur", mandat.alertes[0])

    def test_plafond_par_m2(self):
        mandat = self.creer_mandat(honoraires_bail_bailleur=400, honoraires_bail_locataire=400)
        self.assertEqual(len(mandat.alertes), 1)
        self.assertIn("plafond légal de 390 € pour 39 m²", mandat.alertes[0])
        mandat.bien.zone_tendue = False  # 8 €/m² hors zone tendue
        mandat.honoraires_bail_bailleur = mandat.honoraires_bail_locataire = Decimal("312")
        self.assertEqual(mandat.alertes, [])

    def test_stationnement_non_soumis(self):
        bien = self.creer_bien(usage=Bien.Usage.STATIONNEMENT, surface=None)
        self.assertEqual(self.creer_mandat(bien, honoraires_bail_locataire=50).alertes, [])

    def test_document_word(self):
        mandat = self.creer_mandat(
            date_signature="2026-07-20", assurance_loyers_impayes=True,
            base_gestion=Mandat.BaseGestion.HORS_CHARGES,
            honoraires_bail_bailleur=390, honoraires_bail_locataire=390,
            honoraires_edl_bailleur=Decimal("58.50"), honoraires_edl_locataire=Decimal("58.50"),
        )
        mandat.refresh_from_db()
        texte = texte_docx(generation.generer_docx("mandat_gestion.docx", mandat.contexte_document()))
        self.assertNotIn("{{", texte)
        self.assertIn("MANDAT DE GESTION N° 1", texte)
        self.assertIn("Monsieur DURAND Paul et Madame MARTIN Anne", texte)
        self.assertIn("Adresse du bien\xa0: 12 avenue de Muret 31300 TOULOUSE", texte)
        self.assertIn("Montant\xa0: 899€ Charges comprises dont 45€ de charges", texte)
        self.assertIn("pour une durée de trois ans.", texte)
        self.assertIn("☒Le mandant souhaite adhérer", texte)
        self.assertIn("à 7% TTC", texte)
        self.assertIn("encaissements mensuels (hors charges)", texte)
        self.assertIn("la somme de 780 euros TTC, soit 390 euros TTC à la charge du bailleur et 390 euros", texte)
        self.assertIn("la somme de 117 euros TTC, soit 58,50 euros TTC", texte)
        self.assertIn("Fait à Toulouse le : 20/07/2026", texte)

    def test_montant(self):
        self.assertEqual(generation.montant(Decimal("1234.5")), "1 234,50")
        self.assertEqual(generation.montant(Decimal("390.00")), "390")


@override_settings(MFA_OBLIGATOIRE=False)
class VuesTests(Donnees, TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier)
        reglage = override_settings(MEDIA_ROOT=self.dossier)
        reglage.enable()
        self.addCleanup(reglage.disable)
        self.client.force_login(User.objects.create_user("gestion"))

    def test_creation_depuis_le_bien(self):
        bien = self.creer_bien()
        reponse = self.client.get(reverse("mandats:creer"))
        self.assertContains(reponse, str(bien))
        reponse = self.client.get(reverse("mandats:creer"), {"bien": bien.pk})
        self.assertContains(reponse, "Monsieur DURAND Paul et Madame MARTIN Anne")
        donnees = {
            "bien": bien.pk, "mandant": "Monsieur DURAND Paul", "mandant_adresse": "9 rue des Lilas",
            "mandant_code_postal_ville": "31270 Frouzins", "designation": "Appartement T2",
            "adresse_bien": "12 avenue de Muret", "loyer_cc": "899", "charges": "45",
            "duree_ans": 3, "reconductions": 0, "taux_gestion": "7", "base_gestion": "charges_comprises",
            "taux_assurance": "2.90", "honoraires_bail_bailleur": "0", "honoraires_bail_locataire": "390",
            "honoraires_edl_bailleur": "0", "honoraires_edl_locataire": "0",
        }
        reponse = self.client.post(reverse("mandats:creer"), donnees, follow=True)
        mandat = Mandat.objects.get()
        self.assertRedirects(reponse, mandat.get_absolute_url())
        self.assertContains(reponse, "dépasse celle du bailleur")
        self.assertEqual(bien.mandat_en_cours, mandat)
        # Un bien sous mandat n'est plus proposé pour un nouveau mandat.
        self.assertNotContains(self.client.get(reverse("mandats:creer")), str(bien))

    def test_telechargement_word_et_pdf(self):
        mandat = self.creer_mandat()
        reponse = self.client.get(reverse("mandats:word", args=[mandat.pk]))
        self.assertIn("Monsieur DURAND Paul", texte_docx(reponse.content))
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7") as convertir:
            reponse = self.client.get(reverse("mandats:pdf", args=[mandat.pk]))
        self.assertEqual(reponse["Content-Type"], "application/pdf")
        self.assertEqual(reponse.content, b"%PDF-1.7")
        self.assertTrue(convertir.call_args.args[0].startswith(b"PK"))

    def test_pdf_indisponible(self):
        mandat = self.creer_mandat()
        with mock.patch("documents.views.convertir_pdf", side_effect=ConversionImpossible("hors service")):
            reponse = self.client.get(reverse("mandats:pdf", args=[mandat.pk]), follow=True)
        self.assertContains(reponse, "téléchargez la version Word")

    def test_conversion_gotenberg(self):
        reponse = mock.MagicMock()
        reponse.__enter__.return_value.read.return_value = b"%PDF"
        with mock.patch("urllib.request.urlopen", return_value=reponse) as urlopen:
            self.assertEqual(generation.convertir_pdf(b"docx", "m.docx"), b"%PDF")
        requete = urlopen.call_args.args[0]
        self.assertTrue(requete.full_url.endswith("/forms/libreoffice/convert"))
        self.assertIn(b'filename="m.docx"', requete.data)

    def test_depot_du_mandat_signe(self):
        mandat = self.creer_mandat()
        fichier = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 signe", content_type="application/pdf")
        self.client.post(reverse("mandats:deposer_signe", args=[mandat.pk]), {"fichier_signe": fichier})
        reponse = self.client.get(reverse("mandats:fichier_signe", args=[mandat.pk]))
        self.assertEqual(b"".join(reponse.streaming_content), b"%PDF-1.4 signe")
        reponse = self.client.post(
            reverse("mandats:deposer_signe", args=[mandat.pk]),
            {"fichier_signe": SimpleUploadedFile("virus.exe", b"MZ")}, follow=True,
        )
        self.assertContains(reponse, "Fournir un PDF ou une image")

    def test_registre_et_export(self):
        self.creer_mandat()
        self.creer_mandat(date_fin="2026-01-01")
        self.assertContains(self.client.get(reverse("mandats:registre"), {"etat": "en_cours"}), "1 mandat(s)")
        reponse = self.client.get(reverse("mandats:registre_export"))
        self.assertEqual(load_workbook(io.BytesIO(reponse.content)).active.max_row, 3)

    def test_pages(self):
        mandat = self.creer_mandat()
        for url in [
            reverse("mandats:registre"), reverse("mandats:mandat", args=[mandat.pk]),
            reverse("mandats:modifier", args=[mandat.pk]), reverse("biens:bien", args=[mandat.bien.pk]),
            reverse("accueil"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_modeles_reserves_aux_administrateurs(self):
        self.assertEqual(self.client.get(reverse("documents:modeles")).status_code, 403)

    def test_remplacement_du_modele(self):
        admin = User.objects.create_user("admin")
        attribuer_role(admin, ADMINISTRATEURS)
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("documents:modeles")), "mandant_adresse")

        modele = Document(generation.MODELES_PAR_DEFAUT / "mandat_gestion.docx")
        modele.add_paragraph("Ligne ajoutée par l'agence pour {{ mandant }}.")
        flux = io.BytesIO()
        modele.save(flux)
        url = reverse("documents:deposer", args=["mandat_gestion.docx"])
        self.client.post(url, {"fichier": SimpleUploadedFile("m.docx", flux.getvalue())})
        mandat = self.creer_mandat()
        texte = texte_docx(self.client.get(reverse("mandats:word", args=[mandat.pk])).content)
        self.assertIn("Ligne ajoutée par l'agence pour Monsieur DURAND", texte)

        modele.add_paragraph("{{ balise_inconnue }}")
        flux = io.BytesIO()
        modele.save(flux)
        reponse = self.client.post(url, {"fichier": SimpleUploadedFile("m.docx", flux.getvalue())}, follow=True)
        self.assertContains(reponse, "Balises inconnues : balise_inconnue")

        self.client.post(reverse("documents:retablir", args=["mandat_gestion.docx"]))
        texte = texte_docx(self.client.get(reverse("mandats:word", args=[mandat.pk])).content)
        self.assertNotIn("Ligne ajoutée", texte)
