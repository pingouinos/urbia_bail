import datetime as dt
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from comptes.roles import ADMINISTRATEURS, attribuer_role
from core.models import Evenement, Organisation
from core.testing import creer_gestionnaire, remplir_agence
from contrats.tests import Donnees
from mandats.models import Mandat
from .archives import creer
from .models import PdfArchive, VersionContrat


@override_settings(MFA_OBLIGATOIRE=False)
class ArchivesTests(Donnees, TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = override_settings(MEDIA_ROOT=self.temp.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.gestion = creer_gestionnaire("gestionnaire")
        self.admin = User.objects.create_user("administrateur")
        attribuer_role(self.admin, ADMINISTRATEURS)
        self.client.force_login(self.gestion)
        self.bail = self.creer_bail()
        remplir_agence()
        self.mandat = Mandat.depuis_propriete(self.creer_propriete())
        self.mandat.conclusion = Mandat.Conclusion.AGENCE
        self.mandat.save()
        self.jour = timezone.localdate()

    def url(self, objet, action):
        app = "contrats" if objet.code_type == "bail" else "mandats"
        return reverse(f"{app}:{action}", args=[objet.pk])

    def signer(self, objet, **data):
        objet.refresh_from_db()
        version = objet.versions_document.first()
        if not objet.signe and objet.en_cours and (version is None or version.nature != "preparation"):
            self.preparer(objet)
        return self.client.post(self.url(objet, "deposer_signe"), {"date_signature": self.jour.isoformat(), **data})

    def preparer(self, objet):
        self.client.post(reverse("documents:preparer", args=[objet.code_type, objet.pk]))
        return objet.versions_document.first()

    def scan(self, suffixe=b"un"):
        return SimpleUploadedFile("signe.pdf", b"%PDF-1.7\n" + suffixe, content_type="application/pdf")

    def test_sans_role_refuse_lectures_et_toutes_transitions(self):
        self.client.force_login(User.objects.create_user("sans-role", is_staff=True))
        self.assertEqual(self.client.get(reverse("contrats:liste")).status_code, 403)
        for objet in (self.bail, self.mandat):
            for action in ("deposer_signe", "retirer_signature", "fin"):
                with self.subTest(type=objet.code_type, action=action):
                    self.assertEqual(self.client.post(self.url(objet, action), {}).status_code, 403)
            self.assertEqual(self.client.post(objet.get_absolute_url(), {}).status_code, 403)
            self.assertEqual(self.client.post(reverse("documents:preparer", args=[objet.code_type, objet.pk])).status_code, 403)
        self.assertEqual(VersionContrat.objects.count(), 0)

    def test_dates_signees_verrouillees_pour_tous_les_roles_et_etats(self):
        for user in (self.gestion, self.admin):
            self.client.force_login(user)
            for objet in (self.bail, self.mandat):
                for termine in (False, True):
                    with self.subTest(user=user.username, type=objet.code_type, termine=termine):
                        objet.date_signature = self.jour - dt.timedelta(days=5)
                        field = "date_fin_effective" if objet.code_type == "bail" else "date_fin"
                        setattr(objet, field, self.jour if termine else None)
                        objet.save()
                        self.signer(objet)
                        objet.refresh_from_db()
                        self.assertEqual(objet.date_signature, self.jour - dt.timedelta(days=5))

    def test_docx_et_contexte_figes_apres_changements_du_referentiel(self):
        for objet in (self.bail, self.mandat):
            with self.subTest(type=objet.code_type):
                preparation = self.preparer(objet)
                self.signer(objet)
                objet.refresh_from_db()
                signature = objet.versions_document.first()
                self.assertEqual(bytes(signature.docx), bytes(preparation.docx))
                self.assertEqual(signature.source, preparation)
                objet.propriete.adresse = "NOUVELLE ADRESSE"
                objet.propriete.save()
                objet.propriete.bailleur.nom = "AUTRE BAILLEUR"
                objet.propriete.bailleur.save()
                Organisation.objects.all().update(nom="AUTRE AGENCE")
                dossier = Path(self.temp.name) / "modeles"
                dossier.mkdir(exist_ok=True)
                nom = "bail_habitation" if objet.code_type == "bail" else "mandat_gestion"
                (dossier / f"{nom}.html").write_text("<p>AUTRE MODELE</p>")
                self.assertTrue(bytes(preparation.docx).startswith(b"<!doctype html>"))
                with mock.patch("documents.archives_views.convertir_pdf", side_effect=lambda contenu, *args: contenu):
                    self.assertEqual(self.client.get(self.url(objet, "pdf")).content, bytes(preparation.docx))
                self.assertEqual(objet.contexte_document(), preparation.contexte)
                preparation.refresh_from_db()
                self.assertEqual(preparation.nature, "preparation")

    def test_pdf_envoye_conserve_a_la_signature_et_ne_reconvertit_pas(self):
        preparation = self.preparer(self.bail)
        with mock.patch("documents.archives_views.convertir_pdf", return_value=b"%PDF-envoye") as conversion:
            self.assertEqual(self.client.get(self.url(self.bail, "pdf")).content, b"%PDF-envoye")
            self.assertEqual(self.client.get(self.url(self.bail, "pdf")).content, b"%PDF-envoye")
            self.assertEqual(conversion.call_count, 1)
        self.signer(self.bail)
        with mock.patch("documents.archives_views.convertir_pdf", side_effect=AssertionError("reconversion")):
            self.assertEqual(self.client.get(self.url(self.bail, "pdf")).content, b"%PDF-envoye")
        self.assertTrue(PdfArchive.objects.filter(version=preparation).exists())

    def test_preparation_obsolete_refuse_signature(self):
        self.preparer(self.bail)
        self.bail.propriete.adresse = "Adresse modifiée"
        self.bail.propriete.save()
        reponse = self.signer(self.bail)
        self.assertEqual(reponse.status_code, 302)
        self.bail.refresh_from_db()
        self.assertFalse(self.bail.signe)
        self.assertEqual(self.bail.versions_document.count(), 1)

    def test_scan_tardif_et_remplacement_conservent_les_octets_precedents(self):
        for objet in (self.bail, self.mandat):
            self.signer(objet, fichier_signe=self.scan(b"original"))
            original = objet.versions_document.first()
            self.signer(objet, fichier_signe=self.scan(b"nouveau"))
            original.refresh_from_db()
            nouveau = objet.versions_document.first()
            self.assertEqual(bytes(original.scan), b"%PDF-1.7\noriginal")
            self.assertEqual(bytes(nouveau.scan), b"%PDF-1.7\nnouveau")
            self.assertEqual(nouveau.nature, "scan")
            self.assertEqual(self.client.get(reverse("documents:archive", args=[original.pk, "scan"])).content, bytes(original.scan))

    def test_retrait_conserve_original_et_efface_scan_courant(self):
        self.bail.date_effet = self.jour + dt.timedelta(days=20)
        self.bail.save()
        self.signer(self.bail, fichier_signe=self.scan())
        original = self.bail.versions_document.first()
        self.client.post(self.url(self.bail, "retirer_signature"))
        self.bail.refresh_from_db()
        self.assertFalse(self.bail.signe)
        self.assertFalse(self.bail.fichier_signe)
        self.assertIsNotNone(original.scan)
        self.signer(self.bail)
        self.bail.refresh_from_db()
        self.assertFalse(self.bail.fichier_signe)
        self.assertEqual(self.bail.versions_document.count(), 4)

    def test_correction_administrative_avec_motif_et_original_conserve(self):
        self.signer(self.mandat, fichier_signe=self.scan())
        original = self.mandat.versions_document.first()
        self.client.force_login(self.admin)
        form = self.client.get(self.mandat.get_absolute_url(), {"etape": 1}).context["form"]
        donnees = {nom: form[nom].value() for nom in form.fields if not form.fields[nom].disabled}
        donnees = {nom: "" if val is None else val for nom, val in donnees.items() if val is not False}
        donnees["mandant"] = "Nom corrigé"
        self.client.post(self.mandat.get_absolute_url(), donnees)
        self.mandat.refresh_from_db()
        self.assertNotEqual(self.mandat.mandant, "Nom corrigé")
        self.client.post(self.mandat.get_absolute_url(), {**donnees, "motif": "Erreur de recopie"})
        self.mandat.refresh_from_db()
        self.assertEqual(self.mandat.mandant, "Nom corrigé")
        correction = self.mandat.versions_document.first()
        self.assertEqual((correction.nature, correction.auteur, correction.motif), ("correction", self.admin, "Erreur de recopie"))
        self.assertNotEqual(correction.contexte, original.contexte)
        original.refresh_from_db()
        self.assertEqual(original.nature, "signature")
        self.assertEqual(bytes(original.scan), b"%PDF-1.7\nun")

    def test_historique_ne_se_reconstitue_pas_silencieusement(self):
        self.bail.date_signature = self.jour
        self.bail.save()
        self.assertEqual(self.client.get(self.url(self.bail, "pdf")).status_code, 302)
        self.assertEqual(self.bail.versions_document.count(), 0)
        url = reverse("documents:preparer", args=["bail", self.bail.pk])
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(url)
        version = self.bail.versions_document.first()
        self.assertEqual(version.nature, "reprise")
        self.assertIn("inconnus", version.motif)

    def test_archive_immuable_et_suppression_contrat_interdite(self):
        version = self.preparer(self.bail)
        version.motif = "remplacement interdit"
        with self.assertRaises(ValidationError):
            version.save()
        with self.assertRaises(ValidationError):
            version.delete()
        self.client.force_login(self.admin)
        self.client.post(self.url(self.bail, "supprimer"))
        self.assertTrue(type(self.bail).objects.filter(pk=self.bail.pk).exists())
        # La console Django est réservée au superutilisateur : un administrateur d'agence est renvoyé à sa connexion.
        console = self.client.post(reverse("admin:contrats_bail_change", args=[self.bail.pk]), {})
        self.assertEqual(console.status_code, 302)
        self.assertTrue(console["Location"].startswith(reverse("admin:login")))

    def test_signature_et_archive_atomiques(self):
        self.preparer(self.bail)
        with mock.patch("documents.archives.creer", side_effect=RuntimeError("stockage indisponible")):
            with self.assertRaises(RuntimeError):
                self.signer(self.bail)
        self.bail.refresh_from_db()
        self.assertFalse(self.bail.signe)

    def test_fins_terminees_refusees_aux_gestionnaires(self):
        for objet in (self.bail, self.mandat):
            champ = "date_fin_effective" if objet.code_type == "bail" else "date_fin"
            setattr(objet, champ, self.jour)
            objet.save()
            self.client.post(self.url(objet, "fin"), {champ: (self.jour - dt.timedelta(days=1)).isoformat()})
            objet.refresh_from_db()
            self.assertEqual(getattr(objet, champ), self.jour)
            self.signer(objet)
            objet.refresh_from_db()
            self.assertFalse(objet.signe)

    def test_nouvelle_signature_refusee_sans_preparation(self):
        for objet in (self.bail, self.mandat):
            self.client.post(self.url(objet, "deposer_signe"), {"date_signature": self.jour.isoformat()})
            objet.refresh_from_db()
            self.assertFalse(objet.signe)
            self.assertEqual(objet.versions_document.count(), 0)

    def test_correction_de_fin_exige_motif_et_archive_sans_changer_document(self):
        self.bail.date_effet = self.jour - dt.timedelta(days=20)
        self.bail.save()
        for objet in (self.bail, self.mandat):
            self.signer(objet)
            original = objet.versions_document.first()
            champ = "date_fin_effective" if objet.code_type == "bail" else "date_fin"
            setattr(objet, champ, self.jour)
            objet.save()
            self.client.force_login(self.admin)
            data = {champ: self.jour.isoformat()}
            self.client.post(self.url(objet, "fin"), data)
            self.assertEqual(objet.versions_document.first(), original)
            self.client.post(self.url(objet, "fin"), {**data, "motif": "Correction de saisie"})
            version = objet.versions_document.first()
            self.assertEqual((version.nature, version.motif), ("correction_fin", "Correction de saisie"))
            self.assertEqual(bytes(version.docx), bytes(original.docx))
            self.assertEqual(version.source, original)

    def test_retrait_verrouille_apres_entree_et_fin_pour_les_deux_roles(self):
        for user in (self.gestion, self.admin):
            self.client.force_login(user)
            self.bail.date_effet = self.jour - dt.timedelta(days=10)
            self.bail.date_signature = self.jour - dt.timedelta(days=20)
            self.bail.save()
            self.mandat.date_signature = self.jour - dt.timedelta(days=20)
            self.mandat.date_fin = self.jour
            self.mandat.save()
            for objet in (self.bail, self.mandat):
                self.client.post(self.url(objet, "retirer_signature"))
                objet.refresh_from_db()
                self.assertEqual(objet.date_signature, self.jour - dt.timedelta(days=20))

    def test_superutilisateur_hors_agence_et_archive_inaccessible_sans_role(self):
        self.client.force_login(self.admin)
        version = self.preparer(self.bail)
        url = reverse("documents:archive", args=[version.pk, "pdf"])
        with mock.patch("documents.archives_views.convertir_pdf", return_value=b"%PDF-1.7"):
            self.assertEqual(self.client.get(url).status_code, 200)
        # Seules les archives d'avant le passage au HTML ont un Word.
        self.assertEqual(self.client.get(reverse("documents:archive", args=[version.pk, "docx"])).status_code, 404)
        # Le superutilisateur exploite la plateforme : les documents des agences ne lui sont pas servis.
        self.client.force_login(User.objects.create_superuser("root", "root@example.com", "test-password"))
        self.assertRedirects(self.client.get(url), reverse("plateforme"), fetch_redirect_response=False)
        self.client.force_login(User.objects.create_user("inconnu"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_archive_word_d_avant_le_html(self):
        self.client.force_login(self.admin)
        ancienne = VersionContrat.objects.create(
            bail=self.bail, nature="preparation", auteur=self.admin, auteur_nom="administrateur", motif="Avant le HTML.",
            contexte={}, modele=b"PK\x03\x04modele", docx=b"PK\x03\x04document", empreinte="0" * 64)
        self.assertTrue(ancienne.est_word)
        self.assertEqual(self.client.get(reverse("documents:archive", args=[ancienne.pk, "docx"])).content,
                         b"PK\x03\x04document")
        self.assertContains(self.client.get(self.bail.get_absolute_url()), "Word archivé")
        with mock.patch("documents.archives_views.convertir_pdf", return_value=b"%PDF-1.7") as conversion:
            self.client.get(reverse("documents:archive", args=[ancienne.pk, "pdf"]))
        self.assertEqual(conversion.call_args.args[0], b"PK\x03\x04document")
