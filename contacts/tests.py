import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from candidatures.models import Candidat, Candidature, Garant
from contrats.models import Locataire
from contrats.tests import Donnees
from proprietes.models import Bailleur, Propriete

from .views import contacts


@override_settings(MFA_OBLIGATOIRE=False)
class ContactsTests(Donnees, TestCase):
    """Bailleurs, candidats et locataires dans une même liste, sans doublon,
    avec leurs catégories et leurs liens."""

    def setUp(self):
        self.client.force_login(User.objects.create_user("gestion"))
        self.bailleur = Bailleur.objects.create(civilite="Mme", nom="Durand", prenom="Anne", email="anne@exemple.fr")

    def propriete(self, adresse):
        return Propriete.objects.create(
            bailleur=self.bailleur, adresse=adresse, code_postal="31000", ville="Toulouse", dernier_loyer=700,
        )

    def noms(self, **parametres):
        page = self.client.get(reverse("contacts:liste"), parametres)
        return [(contact.designation, [code for code, _ in contact.etiquettes]) for contact in page.context["page"]]

    def candidature(self, propriete, nom, **autres):
        candidature = Candidature.objects.create(propriete=propriete, **autres)
        Candidat.objects.create(candidature=candidature, nom=nom, prenom="Lina", email=f"{nom.lower()}@exemple.fr")
        return candidature

    def test_une_personne_une_ligne(self):
        lois, muret, taur = self.propriete("3 rue des Lois"), self.propriete("12 avenue de Muret"), self.propriete("5 rue du Taur")
        # Paul Martin : un bail terminé, un bail en cours, écrit différemment.
        self.creer_bail(propriete=lois, locataires=("Martin",), date_effet=dt.date(2020, 1, 1),
                        date_fin_effective=dt.date(2023, 1, 1))
        en_cours = self.creer_bail(propriete=muret, locataires=("MARTIN",), date_effet=dt.date(2024, 9, 1))
        # Anne Durand, bailleur, est aussi candidate ailleurs.
        Candidat.objects.create(candidature=Candidature.objects.create(propriete=taur), nom="durand", prenom="Anne")
        candidature = self.candidature(lois, "Roux")
        Garant.objects.create(candidat=candidature.candidats.get(), nom="Garant", prenom="Paul", adresse="1 rue A")
        self.candidature(lois, "Refuse", statut=Candidature.Statut.NON_RETENUE)
        self.assertEqual(self.noms(), [
            ("DURAND Anne", ["bailleur", "candidat"]), ("MARTIN Paul", ["locataire"]), ("ROUX Lina", ["candidat"]),
        ])
        martin = next(c for c in contacts() if c.nom.lower() == "martin")
        self.assertEqual([(lien.type, lien.propriete_id, lien.precision) for lien in martin.liens],
                         [("bail", muret.pk, ""), ("bail", lois.pk, "terminé")])
        self.assertEqual(martin.url, en_cours.get_absolute_url())
        durand = contacts()[0]
        self.assertEqual((durand.url, durand.email, len(durand.proprietes)), (self.bailleur.get_absolute_url(), "anne@exemple.fr", 3))
        # Recherche et filtres portent sur les liens.
        self.assertEqual(self.noms(categorie="candidat"), [("DURAND Anne", ["bailleur", "candidat"]), ("ROUX Lina", ["candidat"])])
        self.assertEqual(self.noms(categorie="ancien"), [])
        self.assertEqual(self.noms(q="muret"), [("DURAND Anne", ["bailleur", "candidat"]), ("MARTIN Paul", ["locataire"])])
        self.assertEqual(self.noms(propriete=taur.pk), [("DURAND Anne", ["bailleur", "candidat"])])
        self.assertEqual([nom for nom, _ in self.noms(propriete=lois.pk)], ["DURAND Anne", "MARTIN Paul", "ROUX Lina"])
        page = self.client.get(reverse("contacts:liste"))
        self.assertContains(page, "3 rue des Lois")
        self.assertNotContains(page, "GARANT")
        self.assertNotContains(page, "REFUSE")

    def test_homonymes_nes_a_des_dates_differentes(self):
        bail = self.creer_bail(propriete=self.propriete("3 rue des Lois"), locataires=("Martin",))
        Locataire.objects.filter(bail=bail).update(date_naissance=dt.date(1990, 1, 1))
        autre = self.creer_bail(propriete=self.propriete("5 rue du Taur"), locataires=("Martin",))
        Locataire.objects.filter(bail=autre).update(date_naissance=dt.date(1975, 6, 30))
        self.assertEqual([nom for nom, _ in self.noms(categorie="locataire")], ["MARTIN Paul", "MARTIN Paul"])

    def test_bailleur_et_ses_proprietes(self):
        """Un propriétaire de plusieurs biens : une ligne, toutes ses propriétés."""
        for numero in range(1, 6):
            self.propriete(f"{numero} rue des Lois")
        Bailleur.objects.create(nom="Zola", prenom="Emile")
        page = self.client.get(reverse("contacts:liste"), {"categorie": "bailleur", "tri": "proprietes"})
        self.assertEqual([(c.designation, len(c.proprietes)) for c in page.context["page"]], [("DURAND Anne", 5), ("ZOLA Emile", 0)])
        self.assertContains(page, "<summary>5 propriétés</summary>")
        self.assertContains(page, "4 rue des Lois")
        self.assertContains(page, "Aucune propriété")

    def test_menu_et_anciennes_pages(self):
        page = self.client.get(reverse("contacts:liste"))
        self.assertContains(page, ">Contacts</a>")
        self.assertNotContains(page, ">Candidatures</a>")
        self.assertNotContains(page, ">Bailleurs</a>")
        self.assertRedirects(self.client.get(reverse("proprietes:bailleurs"), {"q": "durand"}),
                             f"{reverse('contacts:liste')}?q=durand&categorie=bailleur")
        self.assertEqual(self.client.get(reverse("candidatures:liste")).status_code, 200)
