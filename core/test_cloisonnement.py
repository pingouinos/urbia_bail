"""Cloisonnement des agences : une agence ne voit ni ne touche jamais les
biens, baux, mandats, contacts ou candidatures d'une autre."""

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from candidatures.models import Candidature
from candidatures.tests import Donnees
from comptes.roles import ADMINISTRATEURS, attribuer_role
from contacts.models import Contact
from contrats.models import Bail
from core.testing import creer_gestionnaire, remplir_agence
from mandats.models import Mandat
from proprietes.models import Propriete

from .agence import AgenceInconnue, ParAgence, activer
from .journal import noter
from .models import Evenement, Organisation

# Ce qui n'appartient à aucune agence : les agences elles-mêmes, les indices
# publiés par l'INSEE, la liste officielle des communes en zone tendue, et
# les modèles des documents (rattachés en PR 4).
COMMUNS = {"Organisation", "Membre", "IndiceIRL", "Commune", "ListeCommunes", "Modele"}
APPLICATIONS = {"proprietes", "contacts", "contrats", "mandats", "candidatures", "documents", "core"}


class CloisonnementTests(Donnees, TestCase):
    def setUp(self):
        self.urbia = Organisation.objects.create(nom="URBIA Immobilier")
        self.canal = Organisation.objects.create(nom="Agence du Canal")
        self.elodie = creer_gestionnaire("elodie")
        with activer(self.canal):
            self.lea = creer_gestionnaire("lea")
            remplir_agence()
            self.propriete_canal = self.creer_propriete(adresse="3 quai de Brienne")
            self.bail_canal = self.creer_bail(self.propriete_canal, locataires=("Garnier",))
            self.mandat_canal = Mandat.depuis_propriete(self.propriete_canal)
            self.mandat_canal.conclusion = Mandat.Conclusion.AGENCE
            self.mandat_canal.save()
            self.candidature_canal = self.creer_candidature(self.propriete_canal, candidats=(("Roux", 1800),))
            self.contact_canal = Contact.objects.create(nom="Roux", prenom="Julie")
        with activer(self.urbia):
            remplir_agence()
            self.propriete_urbia = self.creer_propriete(adresse="12 avenue de Muret")

    def test_donnees_rattachees_a_l_agence_qui_les_cree(self):
        self.assertEqual(self.propriete_canal.agence, self.canal)
        self.assertEqual(self.propriete_canal.bailleur.agence, self.canal)
        self.assertEqual((self.bail_canal.agence, self.mandat_canal.agence), (self.canal, self.canal))
        self.assertEqual(self.propriete_urbia.agence, self.urbia)

    def test_listes_limitees_a_l_agence(self):
        self.client.force_login(self.elodie)
        for url in [reverse("proprietes:liste"), reverse("contrats:liste"), reverse("mandats:registre"),
                    reverse("contacts:liste"), reverse("candidatures:liste"), reverse("accueil")]:
            with self.subTest(url=url):
                reponse = self.client.get(url)
                self.assertEqual(reponse.status_code, 200)
                self.assertNotContains(reponse, "quai de Brienne")
                self.assertNotContains(reponse, "Garnier")
                self.assertNotContains(reponse, "Roux")
        self.assertContains(self.client.get(reverse("proprietes:liste")), "avenue de Muret")

    def test_fiches_d_une_autre_agence_introuvables(self):
        self.client.force_login(self.elodie)
        for url in [
            reverse("proprietes:propriete", args=[self.propriete_canal.pk]),
            reverse("proprietes:propriete_modifier", args=[self.propriete_canal.pk]),
            reverse("proprietes:bailleur", args=[self.propriete_canal.bailleur.pk]),
            reverse("contrats:bail", args=[self.bail_canal.pk]),
            reverse("contrats:pdf", args=[self.bail_canal.pk]),
            reverse("mandats:mandat", args=[self.mandat_canal.pk]),
            reverse("candidatures:candidature", args=[self.candidature_canal.pk]),
            reverse("contacts:contact", args=[self.contact_canal.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("proprietes:propriete_supprimer", args=[self.propriete_canal.pk])).status_code, 404,
        )
        self.assertTrue(Propriete.tous.filter(pk=self.propriete_canal.pk).exists())

    def test_formulaire_refuse_le_bien_d_une_autre_agence(self):
        self.client.force_login(self.elodie)
        reponse = self.client.post(reverse("contrats:creer"), self.donnees_bail(self.propriete_canal, ["Martin"]))
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("propriete", reponse.context["form"].errors)
        self.assertEqual(Bail.tous.count(), 1)

    def test_registre_des_mandats_propre_a_chaque_agence(self):
        with activer(self.urbia):
            mandat = Mandat.depuis_propriete(self.propriete_urbia)
            mandat.conclusion = Mandat.Conclusion.AGENCE
            mandat.save()
        # Chaque agence numérote son registre depuis 1.
        self.assertEqual((self.mandat_canal.numero, mandat.numero), (1, 1))

    def test_lien_du_candidat_ouvre_son_agence(self):
        with activer(self.canal):
            # Logement sans bail : celui du 3 quai de Brienne a déjà son prochain locataire.
            candidature = self.creer_candidature(
                self.creer_propriete(adresse="5 quai de Brienne"), candidats=(("Roux", 1800),),
                statut=Candidature.Statut.RETENUE,
            )
            candidature.creer_lien()
        reponse = self.client.get(reverse("formulaire_locataire", args=[candidature.jeton]))
        self.assertContains(reponse, "quai de Brienne")

    def test_sans_agence_rien_n_est_lisible(self):
        with activer(None), self.assertRaises(AgenceInconnue):
            list(Propriete.objects.all())
        with activer(None), self.assertRaises(AgenceInconnue):
            Propriete(bailleur=self.propriete_urbia.bailleur, adresse="x").save()
        # Compte qu'aucune agence ne rattache (créé à la main dans la console).
        orphelin = User.objects.create_user("orphelin")
        attribuer_role(orphelin, ADMINISTRATEURS)
        orphelin.membre.delete()
        self.client.force_login(User.objects.get(pk=orphelin.pk))
        self.assertEqual(self.client.get(reverse("proprietes:liste")).status_code, 403)

    def test_superutilisateur_hors_des_agences_sauf_console(self):
        exploitant = User.objects.create_superuser("exploitant", password="x")
        self.client.force_login(exploitant)
        self.assertRedirects(self.client.get(reverse("proprietes:liste")), reverse("plateforme"))
        console = self.client.get("/admin/proprietes/propriete/")
        self.assertContains(console, "quai de Brienne")
        self.assertContains(console, "avenue de Muret")

    def test_journal_de_chaque_agence(self):
        with activer(self.canal):
            noter(None, "export", "registre du Canal")
        with activer(self.urbia):
            self.assertFalse(Evenement.objects.filter(objet="registre du Canal").exists())
        self.assertEqual(Evenement.tous.get(objet="registre du Canal").agence, self.canal)

    def test_tout_modele_metier_est_cloisonne(self):
        """Un nouveau modèle doit choisir : appartenir à une agence (hériter de
        RattacheAgence ou ParAgence) ou figurer dans COMMUNS."""
        oublies = [
            modele.__name__ for modele in apps.get_models()
            if modele._meta.app_label in APPLICATIONS and not modele.__name__.startswith("Historical")
            and not issubclass(modele, ParAgence) and modele.__name__ not in COMMUNS
        ]
        self.assertEqual(oublies, [])
        for modele in apps.get_models():
            if issubclass(modele, ParAgence):
                with self.subTest(modele=modele.__name__):
                    self.assertTrue(modele.chemins_agence)
                    self.assertIs(modele._default_manager, modele.objects)
