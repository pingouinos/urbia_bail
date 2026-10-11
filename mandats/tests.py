import io
import shutil
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from docx import Document
from openpyxl import load_workbook

from proprietes.models import Bailleur, Propriete
from comptes.roles import ADMINISTRATEURS, attribuer_role
from documents import generation
from documents.generation import ConversionImpossible

from .models import Mandat


def texte_docx(contenu):
    return "\n".join(p.text for p in Document(io.BytesIO(contenu)).paragraphs)


class Donnees:
    def creer_propriete(self, **autres):
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
        return Propriete.objects.create(**valeurs)

    def creer_mandat(self, propriete=None, **autres):
        mandat = Mandat.depuis_propriete(propriete or self.creer_propriete())
        for champ, valeur in autres.items():
            setattr(mandat, champ, valeur)
        mandat.save()
        return mandat


class MandatTests(Donnees, TestCase):
    def test_numeros_du_registre_se_suivent(self):
        propriete = self.creer_propriete()
        numeros = [self.creer_mandat(propriete).numero for _ in range(3)]
        self.assertEqual(numeros, [1, 2, 3])

    def test_prerempli_depuis_les_fiches(self):
        mandat = Mandat.depuis_propriete(self.creer_propriete())
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
        mandat = self.creer_mandat(
            honoraires_bail_bailleur=0, honoraires_bail_locataire=390, honoraires_edl_bailleur=0, honoraires_edl_locataire=117,
        )
        self.assertEqual(len(mandat.alertes), 2)
        self.assertIn("dépasse celle du bailleur", mandat.alertes[0])

    def test_plafond_par_m2(self):
        mandat = self.creer_mandat(honoraires_bail_bailleur=400, honoraires_bail_locataire=400)
        self.assertEqual(len(mandat.alertes), 1)
        self.assertIn("plafond légal de 390 € pour 39 m²", mandat.alertes[0])
        mandat.propriete.zone_tendue = False  # 8 €/m² hors zone tendue
        mandat.honoraires_bail_bailleur = mandat.honoraires_bail_locataire = Decimal("312")
        self.assertEqual(mandat.alertes, [])

    def test_stationnement_non_soumis(self):
        propriete = self.creer_propriete(usage=Propriete.Usage.STATIONNEMENT, surface=None)
        self.assertEqual(self.creer_mandat(propriete, honoraires_bail_locataire=50).alertes, [])

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

    def test_creation_depuis_le_propriete(self):
        propriete = self.creer_propriete()
        reponse = self.client.get(reverse("mandats:creer"))
        self.assertContains(reponse, str(propriete))
        reponse = self.client.get(reverse("mandats:creer"), {"propriete": propriete.pk})
        self.assertContains(reponse, "Monsieur DURAND Paul et Madame MARTIN Anne")
        donnees = {
            "numero": "412", "propriete": propriete.pk, "mandant": "Monsieur DURAND Paul", "mandant_adresse": "9 rue des Lilas",
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
        self.assertEqual(propriete.mandat_en_cours, mandat)
        # Le numéro est celui du registre papier, reporté à la main.
        self.assertEqual(mandat.numero, 412)
        autre = self.creer_propriete(adresse="5 rue du Taur")
        page = self.client.get(reverse("mandats:creer"), {"propriete": autre.pk})
        self.assertContains(page, "Le plus grand numéro déjà saisi ici est le 412.")
        reponse = self.client.post(reverse("mandats:creer"), {**donnees, "propriete": autre.pk})
        self.assertContains(reponse, "Ce numéro est déjà porté par un autre mandat.")
        reponse = self.client.post(reverse("mandats:creer"), {**donnees, "propriete": autre.pk, "numero": ""})
        self.assertIn("numero", reponse.context["form"].errors)
        self.assertEqual(Mandat.objects.count(), 1)
        # Une erreur de recopie se corrige, et la correction reste dans l'historique.
        self.client.post(reverse("mandats:modifier", args=[mandat.pk]), {**donnees, "numero": "413"})
        mandat.refresh_from_db()
        self.assertEqual((mandat.numero, mandat.history.count()), (413, 2))
        # Un bien sous mandat n'est plus proposé pour un nouveau mandat.
        self.assertNotContains(self.client.get(reverse("mandats:creer")), str(propriete))

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

    def test_conversion_impossible_dit_pourquoi(self):
        import urllib.error

        refus = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with mock.patch("urllib.request.urlopen", side_effect=refus), self.assertLogs("documents", "WARNING"):
            with self.assertRaisesMessage(ConversionImpossible, "arrêté ou en cours de redémarrage"):
                generation.convertir_pdf(b"docx")
        # LibreOffice pas encore prêt : une seconde tentative suffit.
        pas_pret = urllib.error.HTTPError("http://gotenberg", 503, "Service Unavailable", {}, io.BytesIO(b""))
        pret = mock.MagicMock()
        pret.__enter__.return_value.read.return_value = b"%PDF"
        with mock.patch("urllib.request.urlopen", side_effect=[pas_pret, pret]), mock.patch("time.sleep"):
            self.assertEqual(generation.convertir_pdf(b"docx"), b"%PDF")
        erreur = urllib.error.HTTPError("http://gotenberg", 500, "Internal", {}, io.BytesIO(b"libreoffice a plante"))
        with mock.patch("urllib.request.urlopen", side_effect=erreur), self.assertLogs("documents", "WARNING") as journal:
            with self.assertRaisesMessage(ConversionImpossible, "erreur 500"):
                generation.convertir_pdf(b"docx")
        self.assertIn("libreoffice a plante", journal.output[0])
        mandat = self.creer_mandat()
        with mock.patch("documents.views.convertir_pdf", side_effect=ConversionImpossible("le service ne répond pas")):
            reponse = self.client.get(reverse("mandats:pdf", args=[mandat.pk]), follow=True)
        self.assertContains(reponse, "indisponible (le service ne répond pas)")

    def test_signature_du_mandat(self):
        """La date de signature ne se saisit pas à la rédaction : elle
        s'enregistre une fois le mandat signé, avec ou sans le scan."""
        propriete = self.creer_propriete()
        page = self.client.get(reverse("mandats:creer"), {"propriete": propriete.pk})
        self.assertNotIn("date_signature", page.context["form"].fields)
        mandat = self.creer_mandat(propriete)
        self.assertContains(self.client.get(mandat.get_absolute_url()), "À signer")
        deposer = reverse("mandats:deposer_signe", args=[mandat.pk])
        demain = timezone.localdate() + timezone.timedelta(days=1)
        reponse = self.client.post(deposer, {"date_signature": demain.isoformat()}, follow=True)
        self.assertContains(reponse, "ne peut pas être dans le futur")
        # La date seule suffit ; le scan suit quand il est disponible.
        reponse = self.client.post(deposer, {"date_signature": "2026-09-15"}, follow=True)
        self.assertContains(reponse, "Signé le 15/09/2026")
        mandat.refresh_from_db()
        self.assertEqual((str(mandat.date_signature), bool(mandat.fichier_signe)), ("2026-09-15", False))
        fichier = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 signe", content_type="application/pdf")
        self.client.post(deposer, {"date_signature": "2026-09-15", "fichier_signe": fichier})
        reponse = self.client.get(reverse("mandats:fichier_signe", args=[mandat.pk]))
        self.assertEqual(b"".join(reponse.streaming_content), b"%PDF-1.4 signe")
        reponse = self.client.post(
            deposer, {"date_signature": "2026-09-15", "fichier_signe": SimpleUploadedFile("virus.exe", b"MZ")}, follow=True,
        )
        self.assertContains(reponse, "Fournir un PDF ou une image")

    def test_honoraires_proposes_au_metre_carre(self):
        """Visite, dossier et bail : plafond par m² selon la zone, reporté sur
        la part du bailleur et celle du locataire."""
        tendue = Mandat.depuis_propriete(self.creer_propriete())
        self.assertEqual((tendue.honoraires_bail_bailleur, tendue.honoraires_bail_locataire), (Decimal("390.00"), Decimal("390.00")))
        ailleurs = Mandat.depuis_propriete(self.creer_propriete(zone_tendue=False, surface=Decimal("52.5")))
        self.assertEqual((ailleurs.honoraires_bail_bailleur, ailleurs.honoraires_bail_locataire), (Decimal("420.00"), Decimal("420.00")))
        # L'état des lieux : 3 €/m² quelle que soit la zone.
        self.assertEqual((tendue.honoraires_edl_bailleur, tendue.honoraires_edl_locataire), (Decimal("117.00"), Decimal("117.00")))
        self.assertEqual((ailleurs.honoraires_edl_bailleur, ailleurs.honoraires_edl_locataire), (Decimal("157.50"), Decimal("157.50")))
        self.assertEqual(ailleurs.alertes, [])
        # Sans surface, ou pour un parking, rien n'est proposé.
        self.assertEqual(Mandat.depuis_propriete(self.creer_propriete(surface=None)).honoraires_bail_locataire, 0)
        page = self.client.get(reverse("mandats:creer"), {"propriete": tendue.propriete.pk})
        self.assertContains(page, "39 m² × 10 €/m² (zone tendue) = 390 €")
        self.assertContains(page, "39 m² × 3 €/m² = 117 €")
        self.assertContains(page, 'name="honoraires_edl_bailleur" value="117.00"')
        self.assertContains(page, 'name="honoraires_bail_locataire" value="390.00"')

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
            reverse("mandats:modifier", args=[mandat.pk]), reverse("proprietes:propriete", args=[mandat.propriete.pk]),
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
