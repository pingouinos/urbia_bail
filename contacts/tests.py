import datetime as dt

from core.testing import creer_gestionnaire

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from candidatures.models import Candidat, Candidature, Garant
from contrats.models import Locataire
from contrats.tests import Donnees
from proprietes.models import Bailleur, Propriete

from .models import Contact


@override_settings(MFA_OBLIGATOIRE=False)
class ContactsTests(Donnees, TestCase):
    """Une fiche par personne : bailleurs, locataires, candidats et garants
    s'y rattachent, et ses catégories se déduisent de ces liens."""

    def setUp(self):
        self.client.force_login(creer_gestionnaire("gestion"))
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

    def test_une_personne_une_fiche(self):
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
            ("DURAND Anne", ["bailleur", "candidat"]), ("GARANT Paul", ["garant"]), ("MARTIN Paul", ["locataire"]),
            ("ROUX Lina", ["candidat"]),
        ])
        # Le candidat non retenu garde sa fiche jusqu'à la purge, hors du carnet.
        self.assertTrue(Contact.objects.filter(nom="Refuse").exists())
        martin = Contact.avec_liens().get(nom__iexact="martin")
        self.assertEqual([(lien.type, lien.propriete_id, lien.precision) for lien in martin.liens],
                         [("bail", muret.pk, ""), ("bail", lois.pk, "terminé")])
        self.assertTrue(martin.automatique)
        durand = Contact.avec_liens().get(nom__iexact="durand")
        self.assertEqual((durand.email, len(durand.proprietes), durand.automatique), ("anne@exemple.fr", 3, False))
        # Filtres, recherche et tri portent sur les liens.
        self.assertEqual(self.noms(categorie="candidat"), [("DURAND Anne", ["bailleur", "candidat"]), ("ROUX Lina", ["candidat"])])
        self.assertEqual(self.noms(categorie="ancien"), [])
        self.assertEqual(self.noms(q="muret"), [("DURAND Anne", ["bailleur", "candidat"]), ("MARTIN Paul", ["locataire"])])
        self.assertEqual(self.noms(propriete=taur.pk), [("DURAND Anne", ["bailleur", "candidat"])])
        self.assertEqual(self.noms(tri="proprietes")[0][0], "DURAND Anne")
        # La fiche réunit tout ce qui concerne la personne.
        page = self.client.get(martin.get_absolute_url())
        self.assertContains(page, "<h2>Baux (2)</h2>")
        self.assertContains(page, en_cours.get_absolute_url())
        page = self.client.get(durand.get_absolute_url())
        self.assertContains(page, "<h2>Propriétés (3)</h2>")
        self.assertContains(page, "<h2>Candidatures (1)</h2>")
        self.assertContains(page, self.bailleur.get_absolute_url())

    def test_homonymes_nes_a_des_dates_differentes(self):
        bail = self.creer_bail(propriete=self.propriete("3 rue des Lois"), locataires=())
        Locataire.objects.create(bail=bail, nom="Martin", prenom="Paul", date_naissance=dt.date(1990, 1, 1))
        Locataire.objects.create(bail=bail, nom="Martin", prenom="Paul", date_naissance=dt.date(1975, 6, 30))
        Locataire.objects.create(bail=bail, nom="MARTIN", prenom="paul", date_naissance=dt.date(1990, 1, 1))
        self.assertEqual(Contact.objects.filter(nom__iexact="martin").count(), 2)

    def test_purge_des_candidats_non_retenus(self):
        """Le contact d'un candidat s'efface avec sa candidature, sauf s'il
        est aussi bailleur ou locataire."""
        lois = self.propriete("3 rue des Lois")
        refusee = self.candidature(lois, "Roux", statut=Candidature.Statut.NON_RETENUE)
        Candidat.objects.create(candidature=refusee, nom="Durand", prenom="Anne")
        Garant.objects.create(candidat=refusee.candidats.first(), nom="Garant", prenom="Paul", adresse="1 rue A")
        self.assertEqual(Contact.objects.count(), 3)
        self.client.post(reverse("candidatures:effacer", args=[refusee.pk]))
        self.assertEqual([c.designation for c in Contact.objects.all()], ["DURAND Anne"])

    def test_creation_par_type(self):
        self.assertContains(self.client.get(reverse("contacts:liste")),
                            f'<a class="bouton-petit bouton-corail" href="{reverse("contacts:nouveau")}">+ Nouveau contact</a>')
        page = self.client.get(reverse("contacts:nouveau"))
        for libelle in ("Propriétaire (bailleur)", "Locataire", "Garant", "Personne", "Entreprise"):
            self.assertContains(page, libelle)
        # Propriétaire : la fiche de bailleur est créée avec le contact.
        reponse = self.client.post(reverse("contacts:nouveau"), {
            "categorie": "bailleur", "nature": "entreprise", "nom": "Bernard SA", "email": "contact@bernard.fr"})
        societe = Contact.objects.get(nom="Bernard SA")
        self.assertRedirects(reponse, societe.get_absolute_url())
        bailleur = societe.bailleurs.get()
        self.assertEqual((bailleur.type, bailleur.email, societe.designation), ("morale", "contact@bernard.fr", "Bernard SA"))
        self.assertEqual([code for code, _ in societe.etiquettes], ["bailleur"])
        # Locataire ou garant : une fiche seule, reliée ensuite par le nom.
        self.client.post(reverse("contacts:nouveau"), {
            "categorie": "locataire", "nature": "personne", "nom": "Petit", "prenom": "Hugo", "telephone": "06 00 00 00 00"})
        hugo = Contact.objects.get(nom="Petit")
        self.assertEqual(([code for code, _ in hugo.etiquettes], hugo.automatique, hugo.liens), (["locataire"], False, []))
        bail = self.creer_bail(propriete=self.propriete("3 rue des Lois"), locataires=())
        locataire = Locataire.objects.create(bail=bail, nom="PETIT", prenom="hugo", email="hugo@exemple.fr")
        hugo = Contact.avec_liens().get(pk=hugo.pk)
        self.assertEqual((locataire.contact, hugo.email, len(hugo.liens)), (hugo, "hugo@exemple.fr", 1))
        # Pas de doublon, pas de personne sans prénom.
        reponse = self.client.post(reverse("contacts:nouveau"), {
            "categorie": "garant", "nature": "personne", "nom": "petit", "prenom": "Hugo"})
        self.assertContains(reponse, "Ce contact existe déjà")
        reponse = self.client.post(reverse("contacts:nouveau"), {"categorie": "garant", "nature": "personne", "nom": "Seul"})
        self.assertIn("prenom", reponse.context["form"].errors)
        self.assertEqual(Contact.objects.count(), 3)

    def test_bailleur_et_contact_restent_alignes(self):
        contact = self.bailleur.contact
        self.assertEqual((contact.designation, contact.email), ("DURAND Anne", "anne@exemple.fr"))
        # La fiche du bailleur fait foi…
        self.bailleur.nom, self.bailleur.telephone = "Durand-Roux", "05 61 00 00 00"
        self.bailleur.save()
        contact.refresh_from_db()
        self.assertEqual((contact.nom, contact.telephone, Contact.objects.count()), ("Durand-Roux", "05 61 00 00 00", 1))
        # … et une modification du contact revient sur le bailleur.
        self.client.post(reverse("contacts:modifier", args=[contact.pk]), {
            "nature": "personne", "civilite": "Mme", "nom": "Durand-Roux", "prenom": "Anne", "email": "nouveau@exemple.fr"})
        self.bailleur.refresh_from_db()
        self.assertEqual((self.bailleur.email, self.bailleur.contact, Contact.objects.count()), ("nouveau@exemple.fr", contact, 1))

    def test_changer_la_nature_depuis_le_contact(self):
        """Passer un contact d'« Entreprise » à « Personne » (ou l'inverse)
        vaut aussi pour sa fiche de bailleur : la modification n'est pas annulée."""
        from proprietes.models import Associe

        societe = Bailleur.objects.create(
            type="sci_familiale", nom="Couet", representant_nom="Couet", representant_prenom="Elodie", siren="732 829 320",
        )
        Associe.objects.create(bailleur=societe, nom="Couet", prenom="Tom")
        contact = societe.contact
        self.assertEqual(contact.nature, "entreprise")
        self.client.post(reverse("contacts:modifier", args=[contact.pk]), {
            "nature": "personne", "civilite": "Mme", "nom": "Couet", "prenom": "Sarah", "email": "sarah@exemple.fr"})
        contact.refresh_from_db()
        societe.refresh_from_db()
        self.assertEqual((contact.nature, contact.prenom, contact.civilite), ("personne", "Sarah", "Mme"))
        self.assertEqual((societe.type, societe.prenom, societe.representant, societe.siren), ("physique", "Sarah", None, ""))
        self.assertFalse(societe.associes.exists())
        self.assertEqual(societe.designation, "Madame COUET Sarah")
        # Et dans l'autre sens : la personne redevient une société.
        self.client.post(reverse("contacts:modifier", args=[contact.pk]), {"nature": "entreprise", "nom": "SCI Couet"})
        contact.refresh_from_db()
        societe.refresh_from_db()
        self.assertEqual((contact.nature, contact.prenom, societe.type, societe.nom), ("entreprise", "", "morale", "SCI Couet"))

    def test_menu_et_anciennes_pages(self):
        page = self.client.get(reverse("contacts:liste"))
        self.assertContains(page, ">Contacts</a>")
        self.assertNotContains(page, ">Candidatures</a>")
        self.assertNotContains(page, ">Bailleurs</a>")
        self.assertRedirects(self.client.get(reverse("proprietes:bailleurs"), {"q": "durand"}),
                             f"{reverse('contacts:liste')}?q=durand&categorie=bailleur")
        self.assertEqual(self.client.get(reverse("candidatures:liste")).status_code, 200)
