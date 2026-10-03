import datetime as dt
import io
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document

from biens.models import Bailleur, Bien
from documents import generation
from mandats.models import Mandat

from .models import Bail, Locataire, ajouter_mois


def texte_docx(contenu):
    return "\n".join(p.text for p in Document(io.BytesIO(contenu)).paragraphs)


class Donnees:
    def creer_bien(self, **autres):
        bailleur = Bailleur.objects.create(
            civilite="Mme", nom="Durand", prenom="Anne", adresse="9 rue des Lilas",
            code_postal="31270", ville="Frouzins",
        )
        valeurs = {
            "bailleur": bailleur, "adresse": "12 avenue de Muret", "code_postal": "31300",
            "ville": "Toulouse", "surface": Decimal("39"), "nb_pieces": 2, "zone_tendue": True,
            "type_habitat": "collectif", "regime_juridique": "copropriete", "periode_construction": "avant_1949",
            "chauffage": "individuel", "eau_chaude": "individuel", "classe_dpe": "D",
            "equipements": "Cuisine équipée\nSalle d'eau avec douche", "dernier_loyer": Decimal("700"),
            "charges": Decimal("50"),
        }
        valeurs.update(autres)
        return Bien.objects.create(**valeurs)

    def creer_bail(self, bien=None, locataires=("Martin",), **autres):
        bail = Bail.depuis_bien(bien or self.creer_bien())
        bail.date_effet = dt.date(2026, 11, 1)
        bail.irl_trimestre = "2e trimestre 2026"
        bail.irl_valeur = Decimal("146.68")
        for champ, valeur in autres.items():
            setattr(bail, champ, valeur)
        bail.save()
        for nom in locataires:
            Locataire.objects.create(bail=bail, civilite="M.", nom=nom, prenom="Paul",
                                     date_naissance=dt.date(1995, 4, 2), lieu_naissance="Albi")
        return bail


class BailTests(Donnees, TestCase):
    def test_ajouter_mois(self):
        self.assertEqual(ajouter_mois(dt.date(2026, 1, 31), 1), dt.date(2026, 2, 28))
        self.assertEqual(ajouter_mois(dt.date(2026, 11, 1), 36), dt.date(2029, 11, 1))

    def test_durees(self):
        bail = self.creer_bail()
        self.assertEqual((bail.duree_mois, bail.date_fin), (36, dt.date(2029, 10, 31)))
        bail.bien.bailleur.type = Bailleur.Type.PERSONNE_MORALE
        self.assertEqual(bail.duree_mois, 72)
        bail.type = Bail.Type.MEUBLE
        self.assertEqual(bail.date_fin, dt.date(2027, 10, 31))
        bail.type = Bail.Type.ETUDIANT
        self.assertEqual(bail.date_fin, dt.date(2027, 7, 31))

    def test_prerempli_depuis_le_bien_et_le_mandat(self):
        bien = self.creer_bien(meuble=True)
        mandat = Mandat.depuis_bien(bien)
        mandat.honoraires_bail_bailleur = mandat.honoraires_bail_locataire = Decimal("390")
        mandat.save()
        bail = Bail.depuis_bien(bien)
        self.assertEqual(bail.type, Bail.Type.MEUBLE)
        self.assertEqual(bail.mandat, mandat)
        self.assertEqual(bail.depot_garantie, Decimal("1400"))  # deux mois en meublé
        self.assertEqual(bail.honoraires_bail_locataire, Decimal("390"))

    def test_alertes(self):
        bail = self.creer_bail(depot_garantie=Decimal("1400"), precedent_loyer=Decimal("650"))
        alertes = " ".join(bail.alertes)
        self.assertIn("Aucun mandat", alertes)
        self.assertIn("Dépôt de garantie supérieur à 1 mois", alertes)
        self.assertIn("Zone tendue : le loyer dépasse celui du précédent locataire", alertes)
        bail.bien.classe_dpe = "F"
        self.assertIn("classé F ou G", " ".join(bail.alertes))

    def test_annexes_selon_le_bien(self):
        bail = self.creer_bail()
        annexes = " ".join(bail.annexes())
        self.assertIn("règlement de copropriété", annexes)
        self.assertIn("plomb", annexes)
        self.assertNotIn("inventaire", annexes)
        bail.type = Bail.Type.MEUBLE
        self.assertIn("inventaire et état détaillé du mobilier", " ".join(bail.annexes()))

    def test_document_nu(self):
        bail = self.creer_bail(locataires=("Martin", "Bernard"), date_signature=dt.date(2026, 10, 20))
        texte = texte_docx(generation.generer_docx("bail_habitation.docx", bail.contexte_document()))
        self.assertNotIn("{{", texte)
        self.assertNotIn("{%", texte)
        self.assertIn("Logement nu", texte)
        self.assertIn("annexe 1 du décret n° 2015-587", texte)
        self.assertIn("Bailleur : Madame DURAND Anne", texte)
        self.assertIn("Locataire : Monsieur MARTIN Paul, né(e) le 02/04/1995 à Albi", texte)
        self.assertIn("Locataire : Monsieur BERNARD Paul", texte)
        self.assertIn("CLAUSE DE SOLIDARITÉ", texte)
        self.assertIn("Durée du contrat : 3 ans, soit jusqu'au 31/10/2029", texte)
        self.assertIn("Montant du loyer mensuel : 700 € hors charges", texte)
        self.assertIn("Cuisine équipée", texte)
        self.assertIn("Montant du dépôt de garantie : 700 €", texte)
        self.assertIn("ne peut excéder un mois de loyer", texte)
        self.assertIn("10 € par m² de surface habitable", texte)
        self.assertIn("Constat de risque d'exposition au plomb", texte)
        self.assertIn("Le 20/10/2026, à Toulouse, en 3 exemplaires originaux.", texte)
        self.assertIn("ENTRETIEN ET REPARATIONS", texte)
        self.assertNotIn("EDF / GDF", texte)

    def test_document_meuble_un_locataire(self):
        bail = self.creer_bail(type=Bail.Type.MEUBLE, depot_garantie=Decimal("1400"))
        texte = texte_docx(generation.generer_docx("bail_habitation.docx", bail.contexte_document()))
        self.assertIn("Logement meublé", texte)
        self.assertIn("annexe 2 du décret n° 2015-587", texte)
        self.assertIn("Durée du contrat : 1 an", texte)
        self.assertIn("ne peut excéder deux mois", texte)
        self.assertIn("inventaire et état détaillé du mobilier", texte)
        self.assertNotIn("CLAUSE DE SOLIDARITÉ", texte)


@override_settings(MFA_OBLIGATOIRE=False)
class VuesTests(Donnees, TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user("gestion"))

    def test_creation_avec_locataires(self):
        bien = self.creer_bien()
        self.assertContains(self.client.get(reverse("baux:creer")), str(bien))
        reponse = self.client.get(reverse("baux:creer"), {"bien": bien.pk})
        self.assertContains(reponse, 'value="700.00"')
        donnees = {
            "bien": bien.pk, "type": "nu", "date_effet": "2026-11-01", "lieu_signature": "Toulouse",
            "loyer": "700", "mode_charges": "provision", "charges": "50", "jour_paiement": "1",
            "a_echoir": "on", "depot_garantie": "700", "irl_trimestre": "2e trimestre 2026",
            "irl_valeur": "146.68", "honoraires_bail_bailleur": "0", "honoraires_bail_locataire": "0",
            "honoraires_edl_bailleur": "0", "honoraires_edl_locataire": "0",
            "locataires-TOTAL_FORMS": "2", "locataires-INITIAL_FORMS": "0",
            "locataires-MIN_NUM_FORMS": "1", "locataires-MAX_NUM_FORMS": "4",
            "locataires-0-civilite": "Mme", "locataires-0-nom": "Martin", "locataires-0-prenom": "Julie",
            "locataires-1-nom": "", "locataires-1-prenom": "",
        }
        reponse = self.client.post(reverse("baux:creer"), donnees)
        bail = Bail.objects.get()
        self.assertRedirects(reponse, bail.get_absolute_url())
        self.assertEqual([str(l) for l in bail.locataires.all()], ["Julie MARTIN"])

    def test_sans_locataire_refuse(self):
        bien = self.creer_bien()
        reponse = self.client.post(reverse("baux:creer"), {
            "bien": bien.pk, "type": "nu", "date_effet": "2026-11-01", "lieu_signature": "Toulouse",
            "loyer": "700", "mode_charges": "provision", "charges": "50", "jour_paiement": "1",
            "depot_garantie": "700", "irl_trimestre": "T2 2026", "irl_valeur": "146.68",
            "honoraires_bail_bailleur": "0", "honoraires_bail_locataire": "0",
            "honoraires_edl_bailleur": "0", "honoraires_edl_locataire": "0",
            "locataires-TOTAL_FORMS": "1", "locataires-INITIAL_FORMS": "0",
            "locataires-MIN_NUM_FORMS": "1", "locataires-MAX_NUM_FORMS": "4",
            "locataires-0-nom": "", "locataires-0-prenom": "",
        })
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(Bail.objects.exists())

    def test_telechargements_et_pages(self):
        bail = self.creer_bail()
        self.assertIn("MARTIN Paul", texte_docx(self.client.get(reverse("baux:word", args=[bail.pk])).content))
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
            self.assertEqual(self.client.get(reverse("baux:pdf", args=[bail.pk])).content, b"%PDF-1.7")
        for url in [reverse("baux:liste"), reverse("baux:bail", args=[bail.pk]),
                    reverse("baux:modifier", args=[bail.pk]), reverse("biens:bien", args=[bail.bien.pk]),
                    reverse("accueil")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
