import io
import json
import shutil
import tempfile
import zipfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from docxtpl import DocxTemplate

from contrats.tests import Donnees
from documents.generation import MODELES_PAR_DEFAUT, generer_docx
from mandats.models import Mandat

from . import docuseal, services
from .models import DemandeSignature, Signataire

CONFIGURE = {"DOCUSEAL_URL": "https://signature.exemple.fr", "DOCUSEAL_CLE_API": "cle",
             "DOCUSEAL_JETON_WEBHOOK": "jeton-secret", "MFA_OBLIGATOIRE": False}


def texte(contenu):
    with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
        return archive.read("word/document.xml").decode()


def reponse_creation(roles, soumission=42):
    return [{"id": 100 + rang, "submission_id": soumission, "role": role} for rang, role in enumerate(roles)]


@override_settings(**CONFIGURE)
class SignatureTests(Donnees, TestCase):
    def setUp(self):
        dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dossier)
        reglage = override_settings(MEDIA_ROOT=dossier)
        reglage.enable()
        self.addCleanup(reglage.disable)
        self.gestion = User.objects.create_user("elodie", email="elodie@urbia.fr", first_name="Elodie",
                                                last_name="Agence")
        self.client.force_login(self.gestion)

    def bail_pret(self, emails=("paul@exemple.fr",)):
        propriete = self.creer_propriete()
        mandat = Mandat.depuis_propriete(propriete)
        mandat.save()
        bail = self.creer_bail(propriete, locataires=[f"Martin{i}" for i in range(len(emails))], mandat=mandat)
        for locataire, email in zip(bail.locataires.all(), emails):
            locataire.email = email
            locataire.save()
        return bail

    def envoyer(self, bail):
        roles = [s["role"] for s in services.signataires_prevus(bail, self.gestion)]
        with mock.patch("signatures.services.convertir_pdf", return_value=b"%PDF"), \
                mock.patch("signatures.docuseal.creer_demande", return_value=reponse_creation(roles)) as creer:
            demande = services.envoyer(bail, self.gestion)
        return demande, creer

    def test_modeles_d_origine_ont_les_emplacements(self):
        """Les balises de signature sont dans les modèles livrés, et invisibles
        sur le document imprimé."""
        for nom in ("bail_habitation.docx", "mandat_gestion.docx"):
            variables = DocxTemplate(MODELES_PAR_DEFAUT / nom).get_undeclared_template_variables()
            self.assertIn("signature_mandataire", variables)
        bail = self.bail_pret()
        self.assertNotIn("role=", texte(generer_docx("bail_habitation.docx", bail.contexte_document())))

    def test_signataires_dans_l_ordre(self):
        bail = self.bail_pret(("paul@exemple.fr", "lea@exemple.fr"))
        prevus = services.signataires_prevus(bail, self.gestion)
        self.assertEqual([s["role"] for s in prevus], ["Locataire 1", "Locataire 2", "Agence"])
        self.assertEqual(prevus[-1]["email"], "elodie@urbia.fr")
        self.assertIn("pour le bailleur", prevus[-1]["qualite"])

    def test_email_manquant_bloque(self):
        bail = self.bail_pret(("",))
        self.assertEqual(services.manques(bail, self.gestion), ["Ajoutez l'e-mail de Paul MARTIN0, locataire."])
        with self.assertRaises(services.SignatureImpossible):
            services.envoyer(bail, self.gestion)

    @override_settings(DOCUSEAL_URL="")
    def test_sans_configuration_rien_ne_change(self):
        bail = self.bail_pret()
        page = self.client.get(bail.get_absolute_url())
        self.assertFalse(page.context["signature"]["active"])
        self.assertNotContains(page, "Envoyer en signature")

    def test_envoi_place_les_balises(self):
        bail = self.bail_pret(("paul@exemple.fr", "lea@exemple.fr"))
        with mock.patch("signatures.services.convertir_pdf", return_value=b"%PDF") as convertir, \
                mock.patch("signatures.docuseal.creer_demande",
                           return_value=reponse_creation(["Locataire 1", "Locataire 2", "Agence"])) as creer:
            demande = services.envoyer(bail, self.gestion)
        document = texte(convertir.call_args.args[0])
        for role in ("Locataire 1", "Locataire 2", "Agence"):
            self.assertIn(f"{{{{Signature {role};type=signature;role={role};", document)
        self.assertIn("type=datenow;role=Agence", document)
        envoyes = creer.call_args.args[2]
        self.assertEqual([s["email"] for s in envoyes], ["paul@exemple.fr", "lea@exemple.fr", "elodie@urbia.fr"])
        self.assertEqual(demande.soumission, 42)
        self.assertEqual([s.identifiant for s in demande.signataires.all()], [100, 101, 102])
        # Une seule demande à la fois.
        self.assertEqual(services.manques(bail, self.gestion), ["Ce contrat est déjà chez les signataires."])

    def test_modele_sans_emplacement_refuse(self):
        bail = self.bail_pret()
        with mock.patch("signatures.services.generer_docx", return_value=generer_docx(
                "bail_habitation.docx", bail.contexte_document())):
            with self.assertRaisesRegex(services.SignatureImpossible, "n'a pas d'emplacement de signature"):
                services.envoyer(bail, self.gestion)

    def test_tout_signe_range_le_pdf(self):
        bail = self.bail_pret()
        demande, _ = self.envoyer(bail)
        distant = {
            "status": "completed", "completed_at": "2026-10-16T09:30:00Z",
            "combined_document_url": "https://signature.exemple.fr/file/abc",
            "submitters": [{"id": 100, "status": "completed", "completed_at": "2026-10-15T18:00:00Z"},
                           {"id": 101, "status": "completed", "completed_at": "2026-10-16T09:30:00Z"}],
        }
        with mock.patch("signatures.docuseal.demande", return_value=distant), \
                mock.patch("signatures.docuseal.telecharger", return_value=b"%PDF signe"):
            services.synchroniser(demande)
        bail.refresh_from_db()
        self.assertEqual(str(bail.date_signature), "2026-10-16")
        self.assertEqual(bail.fichier_signe.read(), b"%PDF signe")
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeSignature.Statut.SIGNEE)
        self.assertTrue(all(s.statut == Signataire.Statut.SIGNE for s in demande.signataires.all()))

    def test_en_attente_puis_refus(self):
        bail = self.bail_pret()
        demande, _ = self.envoyer(bail)
        distant = {"status": "pending", "submitters": [{"id": 100, "status": "opened"}, {"id": 101, "status": "awaiting"}]}
        with mock.patch("signatures.docuseal.demande", return_value=distant):
            services.synchroniser(demande)
        self.assertEqual([s.statut for s in demande.signataires.all()], ["ouvert", "attente"])
        distant["submitters"][0]["status"] = "declined"
        with mock.patch("signatures.docuseal.demande", return_value=distant):
            services.synchroniser(demande)
        self.assertEqual(demande.statut, DemandeSignature.Statut.REFUSEE)
        bail.refresh_from_db()
        self.assertFalse(bail.signe)

    def test_page_avant_et_pendant(self):
        bail = self.bail_pret()
        page = self.client.get(bail.get_absolute_url())
        self.assertContains(page, "Envoyer en signature à 2 personnes")
        self.assertContains(page, "Signé sur papier ?")
        roles = ["Locataire 1", "Agence"]
        with mock.patch("signatures.services.convertir_pdf", return_value=b"%PDF"), \
                mock.patch("signatures.docuseal.creer_demande", return_value=reponse_creation(roles)), \
                mock.patch("signatures.docuseal.demande", return_value={"status": "pending", "submitters": []}):
            reponse = self.client.post(reverse("signatures:envoyer", args=["bail", bail.pk]), follow=True)
        self.assertContains(reponse, "Envoyé en signature")
        distant = {"status": "pending", "submitters": [{"id": 100, "status": "completed",
                                                        "completed_at": "2026-10-15T18:00:00Z"}]}
        DemandeSignature.objects.update(synchronisee_le=None)
        with mock.patch("signatures.docuseal.demande", return_value=distant):
            page = self.client.get(bail.get_absolute_url())
        self.assertContains(page, "Signé le 15/10")
        self.assertContains(page, "Relancer")
        self.assertContains(page, "Annuler l'envoi")
        # Le bail ne se modifie pas pendant que les signataires l'ont en main.
        reponse = self.client.post(bail.get_absolute_url(), self.donnees_bail(bail.propriete, ["Martin0"]), follow=True)
        self.assertContains(reponse, "annuler d&#x27;abord l&#x27;envoi")

    def test_relance_et_annulation(self):
        bail = self.bail_pret()
        demande, _ = self.envoyer(bail)
        signataire = demande.signataires.first()
        with mock.patch("signatures.docuseal.relancer") as relancer:
            self.client.post(reverse("signatures:relancer", args=[signataire.pk]))
        relancer.assert_called_once_with(100)
        signataire.refresh_from_db()
        self.assertIsNotNone(signataire.relance_le)
        with mock.patch("signatures.docuseal.archiver") as archiver:
            self.client.post(reverse("signatures:annuler", args=[demande.pk]))
        archiver.assert_called_once_with(42)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeSignature.Statut.ANNULEE)
        self.assertEqual(services.manques(bail, self.gestion), [])

    def test_retour_docuseal(self):
        bail = self.bail_pret()
        demande, _ = self.envoyer(bail)
        adresse = reverse("signatures:retour_docuseal", args=["jeton-secret"])
        corps = json.dumps({"event_type": "form.completed", "data": {"id": 100, "submission": {"id": 42}}})
        self.client.logout()
        with mock.patch("signatures.services.synchroniser") as synchroniser:
            reponse = self.client.post(adresse, corps, content_type="application/json")
        self.assertEqual(reponse.status_code, 200)
        synchroniser.assert_called_once_with(demande)
        # Mauvais jeton : l'adresse n'existe pas.
        reponse = self.client.post(reverse("signatures:retour_docuseal", args=["autre"]), corps,
                                   content_type="application/json")
        self.assertEqual(reponse.status_code, 404)

    def test_mandat(self):
        propriete = self.creer_propriete()
        propriete.bailleur.email = "anne@exemple.fr"
        propriete.bailleur.save()
        mandat = Mandat.depuis_propriete(propriete)
        mandat.save()
        prevus = services.signataires_prevus(mandat, self.gestion)
        self.assertEqual([(s["role"], s["email"]) for s in prevus],
                         [("Mandant", "anne@exemple.fr"), ("Agence", "elodie@urbia.fr")])
        with mock.patch("signatures.services.convertir_pdf", return_value=b"%PDF") as convertir, \
                mock.patch("signatures.docuseal.creer_demande", return_value=reponse_creation(["Mandant", "Agence"])):
            services.envoyer(mandat, self.gestion)
        document = texte(convertir.call_args.args[0])
        self.assertIn("role=Mandant;", document)
        self.assertIn("role=Agence;", document)


class ClientTests(TestCase):
    @override_settings(DOCUSEAL_URL="", DOCUSEAL_CLE_API="")
    def test_non_configure(self):
        with self.assertRaises(docuseal.DocusealIndisponible):
            docuseal.demande(1)

    @override_settings(**CONFIGURE)
    def test_reponse_sous_forme_de_demande(self):
        with mock.patch("signatures.docuseal._appeler", return_value={"id": 7, "submitters": [{"id": 1, "role": "A"}]}):
            self.assertEqual(docuseal.creer_demande("Bail", b"%PDF", []), [{"id": 1, "role": "A", "submission_id": 7}])
