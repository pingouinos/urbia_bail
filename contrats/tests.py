import datetime as dt
import io
from decimal import Decimal
from unittest import mock

from core.testing import creer_gestionnaire
from comptes.roles import ADMINISTRATEURS, attribuer_role

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from docx import Document

from proprietes.models import Bailleur, Propriete
from documents import generation
from mandats.models import Mandat

from .models import SUIVI_EDL_DEPUIS, Bail, Locataire, ajouter_mois


def texte_docx(contenu):
    return "\n".join(p.text for p in Document(io.BytesIO(contenu)).paragraphs)


class Donnees:
    def creer_propriete(self, **autres):
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
        return Propriete.objects.create(**valeurs)

    def creer_bail(self, propriete=None, locataires=("Martin",), **autres):
        bail = Bail.depuis_propriete(propriete or self.creer_propriete())
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

    def donnees_bail(self, propriete, noms):
        donnees = {
            "propriete": propriete.pk, "type": "nu", "date_effet": "2026-11-01", "lieu_signature": "Toulouse",
            "loyer": "700", "mode_charges": "provision", "charges": "50", "jour_paiement": "1",
            "depot_garantie": "700", "irl_trimestre": "2e trimestre 2026", "irl_valeur": "146.68",
            "honoraires_bail_bailleur": "0", "honoraires_bail_locataire": "0",
            "honoraires_edl_bailleur": "0", "honoraires_edl_locataire": "0",
            "locataires-TOTAL_FORMS": str(len(noms)), "locataires-INITIAL_FORMS": "0",
            "locataires-MIN_NUM_FORMS": "1", "locataires-MAX_NUM_FORMS": "6",
        }
        for i, nom in enumerate(noms):
            donnees[f"locataires-{i}-nom"] = nom
            donnees[f"locataires-{i}-prenom"] = "Léa" if nom else ""
        return donnees


class BailTests(Donnees, TestCase):
    def test_ajouter_mois(self):
        self.assertEqual(ajouter_mois(dt.date(2026, 1, 31), 1), dt.date(2026, 2, 28))
        self.assertEqual(ajouter_mois(dt.date(2026, 11, 1), 36), dt.date(2029, 11, 1))

    def test_durees(self):
        bail = self.creer_bail()
        self.assertEqual((bail.duree_mois, bail.date_fin), (36, dt.date(2029, 10, 31)))
        bail.propriete.bailleur.type = Bailleur.Type.PERSONNE_MORALE
        self.assertEqual(bail.duree_mois, 72)
        bail.type = Bail.Type.MEUBLE
        self.assertEqual(bail.date_fin, dt.date(2027, 10, 31))
        bail.type = Bail.Type.ETUDIANT
        self.assertEqual(bail.date_fin, dt.date(2027, 7, 31))

    def test_prerempli_depuis_le_propriete_et_le_mandat(self):
        propriete = self.creer_propriete(meuble=True)
        mandat = Mandat.depuis_propriete(propriete)
        mandat.honoraires_bail_bailleur = mandat.honoraires_bail_locataire = Decimal("390")
        mandat.save()
        bail = Bail.depuis_propriete(propriete)
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
        bail.propriete.classe_dpe = "F"
        self.assertIn("classé F ou G", " ".join(bail.alertes))

    def test_annexes_selon_le_propriete(self):
        bail = self.creer_bail()
        annexes = " ".join(bail.annexes())
        self.assertIn("règlement de copropriété", annexes)
        self.assertIn("plomb", annexes)
        self.assertNotIn("inventaire", annexes)
        bail.type = Bail.Type.MEUBLE
        self.assertIn("inventaire et état détaillé du mobilier", " ".join(bail.annexes()))

    def test_document_nu(self):
        bail = self.creer_bail(locataires=("Martin", "Bernard"), date_signature=dt.date(2026, 10, 20),
                               garants="Mme Anne Roux, 1 place du Capitole, 31000 Toulouse\nM. Marc Roux, idem")
        texte = texte_docx(generation.generer_docx("bail_habitation.docx", bail.contexte_document()))
        self.assertNotIn("{{", texte)
        self.assertNotIn("{%", texte)
        self.assertIn("Logement nu", texte)
        self.assertIn("annexe 1 du décret n° 2015-587", texte)
        self.assertIn("Bailleur : Madame DURAND Anne", texte)
        self.assertIn("Locataire : Monsieur MARTIN Paul, né(e) le 02/04/1995 à Albi", texte)
        self.assertIn("Locataire : Monsieur BERNARD Paul", texte)
        self.assertIn("CLAUSE DE SOLIDARITÉ", texte)
        self.assertIn("Garant : Mme Anne Roux, 1 place du Capitole, 31000 Toulouse", texte)
        self.assertIn("Garant : M. Marc Roux, idem", texte)
        self.assertNotIn("Garant de ce locataire", texte)
        self.assertIn("Cette solidarité cesse dans les conditions prévues par l'article 8-1", texte)
        self.assertNotIn("colocation", texte)
        self.assertNotIn("six mois après la date d'effet du congé", texte)
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

    def test_document_colocation(self):
        bail = self.creer_bail(locataires=("Martin", "Bernard"), colocation=True)
        bail.locataires.filter(nom="Bernard").update(
            garants="Mme Claire Bernard, 3 rue Alsace, 31000 Toulouse\n\nM. Luc Bernard, 3 rue Alsace, 31000 Toulouse"
        )
        texte = texte_docx(generation.generer_docx("bail_habitation.docx", bail.contexte_document()))
        self.assertNotIn("{{", texte)
        self.assertNotIn("{%", texte)
        self.assertIn("Le logement est loué en colocation, au sens de l'article 8-1", texte)
        self.assertIn("Garant de ce locataire : Mme Claire Bernard, 3 rue Alsace, 31000 Toulouse", texte)
        self.assertIn("Garant de ce locataire : M. Luc Bernard, 3 rue Alsace, 31000 Toulouse", texte)
        self.assertEqual(texte.count("Garant de ce locataire"), 2)
        self.assertIn("prennent fin à la date d'effet de son congé lorsqu'un nouveau colocataire figure au bail", texte)
        self.assertIn("au plus tard six mois après la date d'effet du congé", texte)
        self.assertNotIn("Colocation cochée", " ".join(bail.alertes))

    def test_colocation_avec_un_seul_locataire(self):
        bail = self.creer_bail(colocation=True)
        self.assertIn("Colocation cochée avec un seul locataire", " ".join(bail.alertes))
        texte = texte_docx(generation.generer_docx("bail_habitation.docx", bail.contexte_document()))
        self.assertNotIn("loué en colocation", texte)


@override_settings(MFA_OBLIGATOIRE=False)
class VuesTests(Donnees, TestCase):
    def setUp(self):
        self.client.force_login(creer_gestionnaire("gestion"))

    def test_suivi_des_loyers(self):
        """Échéancier d'un bail, pointage d'un paiement, avis d'échéance et quittance."""
        from unittest import mock

        from documents import generation

        from .models import Echeance

        bail = self.creer_bail(date_effet=dt.date(2026, 3, 16), date_signature=dt.date(2026, 3, 1))
        loyers = bail.get_absolute_url() + "#loyers"
        # Pas avant la prise d'effet du bail.
        reponse = self.client.post(reverse("contrats:loyers_suivre", args=[bail.pk]), {"depuis": "2026-01"}, follow=True)
        self.assertContains(reponse, "ne peut pas commencer avant la prise")
        with mock.patch("django.utils.timezone.localdate", return_value=dt.date(2026, 5, 10)):
            reponse = self.client.post(reverse("contrats:loyers_suivre", args=[bail.pk]), {"depuis": "2026-03"})
            self.assertRedirects(reponse, loyers, fetch_redirect_response=False)
            mars, avril, mai, juin = bail.echeances.order_by("periode")
            # Mars : entrée le 16, soit 16 jours sur 31.
            self.assertEqual((mars.debut, mars.fin, mars.partielle), (dt.date(2026, 3, 16), dt.date(2026, 3, 31), True))
            self.assertEqual(mars.loyer, (bail.loyer * 16 / 31).quantize(Decimal("0.01")))
            self.assertEqual((avril.loyer, avril.charges, avril.partielle), (bail.loyer, bail.charges, False))
            self.assertEqual((avril.exigible_le, juin.exigible_le), (dt.date(2026, 4, 1), dt.date(2026, 6, 1)))
            # Rejouer ne recrée rien.
            self.assertEqual(bail.completer_echeancier(), [])
            self.assertEqual((mars.etat[1], mai.etat[1], juin.etat[1]), ("Impayé", "Impayé", "À venir"))
            # La quittance n'existe qu'une fois le loyer payé ; un paiement futur est refusé.
            reponse = self.client.get(reverse("contrats:echeance_quittance", args=[avril.pk]), follow=True)
            self.assertContains(reponse, "une fois le loyer payé")
            pointer = reverse("contrats:echeance_pointer", args=[avril.pk])
            self.assertContains(self.client.post(pointer, {"paye_le": "2026-06-01"}, follow=True), "ne peut pas être dans le futur")
            self.client.post(pointer, {"paye_le": "2026-04-03"})
            avril.refresh_from_db()
            self.assertEqual(avril.etat, ("loue", "Payé le 03/04/2026"))
            with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
                reponse = self.client.get(reverse("contrats:echeance_quittance", args=[avril.pk]))
            self.assertEqual(reponse["Content-Type"], "application/pdf")
            self.assertIn("Quittance avril 2026 MARTIN", reponse["Content-Disposition"])
            # Les loyers en retard remontent dans les actions en attente.
            self.assertContains(self.client.get(reverse("accueil")), "2 loyers impayés : Paul MARTIN")
        texte = texte_docx(generation.generer_docx("quittance.docx", avril.contexte_document()))
        self.assertNotIn("{{", texte)
        self.assertIn("QUITTANCE DE LOYER", texte)
        self.assertIn("Paiement reçu le 03/04/2026.", texte)
        self.assertIn("du 01/04/2026 au 30/04/2026", texte)
        avis = texte_docx(generation.generer_docx("avis_echeance.docx", mars.contexte_document()))
        self.assertIn("au plus tard le 16/03/2026", avis)
        # Annuler un pointage, puis arrêter le suivi tant que rien n'est pointé.
        self.client.post(pointer, {"action": "annuler"})
        self.assertFalse(Echeance.objects.filter(paye_le__isnull=False).exists())
        self.client.post(reverse("contrats:loyers_suivre", args=[bail.pk]), {"action": "arreter"})
        bail.refresh_from_db()
        self.assertEqual((bail.suivi_loyers_depuis, bail.echeances.count()), (None, 0))

    def test_echeancier_s_arrete_a_la_sortie(self):
        from unittest import mock

        bail = self.creer_bail(date_effet=dt.date(2026, 1, 1), date_fin_effective=dt.date(2026, 2, 10), a_echoir=False)
        bail.suivi_loyers_depuis = dt.date(2026, 1, 1)
        bail.save()
        with mock.patch("django.utils.timezone.localdate", return_value=dt.date(2026, 6, 1)):
            bail.completer_echeancier()
        janvier, fevrier = bail.echeances.order_by("periode")
        # Dernier mois au prorata : 10 jours sur 28 ; à terme échu, le loyer est dû le mois suivant.
        self.assertEqual((fevrier.fin, fevrier.loyer), (dt.date(2026, 2, 10), (bail.loyer * 10 / 28).quantize(Decimal("0.01"))))
        self.assertEqual((janvier.exigible_le, fevrier.exigible_le), (dt.date(2026, 2, 1), dt.date(2026, 3, 1)))

    def test_signature_du_bail(self):
        """Déposer l'exemplaire signé enregistre aussi la date : le bail quitte
        l'étape « Signature ». Modifier le bail ensuite ne l'efface pas."""
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile

        bail = self.creer_bail()
        page = self.client.get(bail.get_absolute_url())
        self.assertContains(page, "À signer")
        self.assertNotIn("date_signature", self.client.get(bail.get_absolute_url()).context["form"].fields)
        deposer = reverse("contrats:deposer_signe", args=[bail.pk])
        with tempfile.TemporaryDirectory() as dossier, self.settings(MEDIA_ROOT=dossier):
            scan = SimpleUploadedFile("bail.pdf", b"%PDF-1.7 signe", content_type="application/pdf")
            self.client.post(reverse("documents:preparer", args=["bail", bail.pk]))
            reponse = self.client.post(deposer, {"date_signature": "2026-10-10", "fichier_signe": scan}, follow=True)
            self.assertContains(reponse, "Signé le 10/10/2026")
            bail.refresh_from_db()
            self.assertEqual((bail.signe, str(bail.date_signature)), (True, "2026-10-10"))
            self.assertRegex(bail.fichier_signe.name, r"^baux/[0-9a-f]{32}\.pdf$")
            # Le scan sans date est refusé : c'est la date qui dit que le bail est signé.
            autre = self.creer_bail(self.creer_propriete(adresse="5 rue du Taur"))
            scan = SimpleUploadedFile("bail.pdf", b"%PDF-1.7", content_type="application/pdf")
            reponse = self.client.post(reverse("contrats:deposer_signe", args=[autre.pk]), {"date_signature": "", "fichier_signe": scan}, follow=True)
            autre.refresh_from_db()
            self.assertFalse(autre.signe or autre.fichier_signe)
        # Enregistrer le formulaire du bail ne touche pas à la date de signature.
        from django.forms.models import model_to_dict

        from .forms import BailForm

        donnees = {nom: valeur for nom, valeur in model_to_dict(bail, fields=BailForm(instance=bail).fields).items()
                   if valeur not in (None, "")}
        form = BailForm({**donnees, "lieu_signature": "Muret"}, instance=bail)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        bail.refresh_from_db()
        self.assertEqual((str(bail.date_signature), bail.lieu_signature), ("2026-10-10", "Muret"))

    def test_supprimer_un_bail(self):
        """Un bail sans signature ni archive se supprime ; un bail signé est conservé."""
        from candidatures.models import Candidat, Candidature
        from comptes.roles import ADMINISTRATEURS, attribuer_role

        propriete = self.creer_propriete()
        brouillon = self.creer_bail(propriete)
        page = self.client.get(brouillon.get_absolute_url())
        self.assertContains(page, reverse("contrats:supprimer", args=[brouillon.pk]))
        self.assertEqual(self.client.get(reverse("contrats:supprimer", args=[brouillon.pk])).status_code, 405)
        reponse = self.client.post(reverse("contrats:supprimer", args=[brouillon.pk]), follow=True)
        self.assertRedirects(reponse, propriete.get_absolute_url())
        self.assertContains(reponse, "Bail supprimé. Le logement est de nouveau proposé à la location.")
        propriete.refresh_from_db()
        self.assertTrue(propriete.a_louer and not Bail.objects.exists())
        # Né d'une candidature retenue : elle retrouve son étape.
        candidature = Candidature.objects.create(propriete=propriete, statut="retenue")
        Candidat.objects.create(candidature=candidature, nom="Roux", prenom="Lina")
        bail = self.creer_bail(propriete)
        candidature.bail = bail
        candidature.save()
        reponse = self.client.post(reverse("contrats:supprimer", args=[bail.pk]), follow=True)
        self.assertContains(reponse, "La candidature retenue est conservée")
        candidature.refresh_from_db()
        self.assertEqual((candidature.bail, candidature.statut), (None, "retenue"))
        # Signé : suppression refusée à tous les rôles pour conserver les archives.
        signe = self.creer_bail(propriete, date_signature=dt.date(2026, 10, 1))
        self.assertNotContains(self.client.get(signe.get_absolute_url()), reverse("contrats:supprimer", args=[signe.pk]))
        reponse = self.client.post(reverse("contrats:supprimer", args=[signe.pk]), follow=True)
        self.assertContains(reponse, "conservez-le et utilisez la fin du bail")
        self.assertTrue(Bail.objects.filter(pk=signe.pk).exists())
        admin = User.objects.create_user("admin")
        attribuer_role(admin, ADMINISTRATEURS)
        self.client.force_login(admin)
        self.assertNotContains(self.client.get(signe.get_absolute_url()), reverse("contrats:supprimer", args=[signe.pk]))
        self.client.post(reverse("contrats:supprimer", args=[signe.pk]))
        self.assertTrue(Bail.objects.filter(pk=signe.pk).exists())

    def test_nouveau_contrat_laisse_le_choix(self):
        page = self.client.get(reverse("contrats:choisir"))
        self.assertContains(page, reverse("mandats:creer"))
        self.assertContains(page, reverse("contrats:creer"))
        self.assertContains(self.client.get(reverse("contrats:liste")), f'href="{reverse("contrats:choisir")}">+ Nouveau contrat')
        # Le tableau de bord ne propose plus de création : elle part des listes ou du menu « Ajouter ».
        self.assertNotContains(self.client.get(reverse("accueil")), "+ Nouveau contrat")

    def test_creation_avec_locataires(self):
        propriete = self.creer_propriete()
        self.assertContains(self.client.get(reverse("contrats:creer")), str(propriete))
        reponse = self.client.get(reverse("contrats:creer"), {"propriete": propriete.pk})
        self.assertContains(reponse, 'value="700.00"')
        donnees = {
            "propriete": propriete.pk, "type": "nu", "date_effet": "2026-11-01", "lieu_signature": "Toulouse",
            "loyer": "700", "mode_charges": "provision", "charges": "50", "jour_paiement": "1",
            "a_echoir": "on", "depot_garantie": "700", "irl_trimestre": "2e trimestre 2026",
            "irl_valeur": "146.68", "honoraires_bail_bailleur": "0", "honoraires_bail_locataire": "0",
            "honoraires_edl_bailleur": "0", "honoraires_edl_locataire": "0",
            "locataires-TOTAL_FORMS": "2", "locataires-INITIAL_FORMS": "0",
            "locataires-MIN_NUM_FORMS": "1", "locataires-MAX_NUM_FORMS": "4",
            "locataires-0-civilite": "Mme", "locataires-0-nom": "Martin", "locataires-0-prenom": "Julie",
            "locataires-1-nom": "", "locataires-1-prenom": "",
        }
        reponse = self.client.post(reverse("contrats:creer"), donnees)
        bail = Bail.objects.get()
        self.assertRedirects(reponse, bail.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual([str(l) for l in bail.locataires.all()], ["Julie MARTIN"])
        # Fin du parcours : la page du bail le confirme une seule fois.
        page = self.client.get(bail.get_absolute_url())
        self.assertContains(page, "Bail enregistré : il se télécharge")
        self.assertContains(page, reverse("contrats:pdf", args=[bail.pk]))
        self.assertNotContains(self.client.get(bail.get_absolute_url()), "Bail enregistré")

    def test_recherche_du_logement(self):
        muret = self.creer_propriete()
        autre = self.creer_propriete(adresse="3 rue Alsace-Lorraine", ref_ics="L-0042")
        page = self.client.get(reverse("contrats:creer"), {"q": "muret toulouse"})
        self.assertContains(page, str(muret))
        self.assertNotContains(page, str(autre))
        page = self.client.get(reverse("contrats:creer"), {"q": "L-0042"})
        self.assertContains(page, str(autre))
        self.assertNotContains(page, str(muret))
        self.assertContains(self.client.get(reverse("contrats:creer"), {"q": "introuvable"}), "Aucun logement")

    LOGEMENT = {
        "logement-adresse": "5 place du Capitole", "logement-code_postal": "31000", "logement-ville": "Toulouse",
        "logement-type_habitat": "collectif", "logement-regime_juridique": "copropriete",
        "logement-periode_construction": "avant_1949", "logement-surface": "45", "logement-nb_pieces": "2",
        "logement-chauffage": "individuel", "logement-eau_chaude": "individuel", "logement-classe_dpe": "C",
        "logement-dernier_loyer": "820", "logement-charges": "40",
    }

    def test_nouveau_logement_et_proprietaire(self):
        reponse = self.client.post(reverse("contrats:nouveau_logement"), {
            **self.LOGEMENT, "proprietaire-type": "physique", "proprietaire-civilite": "M.",
            "proprietaire-nom": "Garcia", "proprietaire-prenom": "Luis",
        })
        propriete = Propriete.objects.get()
        self.assertRedirects(reponse, f"{reverse('contrats:creer')}?propriete={propriete.pk}")
        self.assertEqual((propriete.bailleur.nom, propriete.dernier_loyer, propriete.ref_ics), ("Garcia", Decimal("820"), None))
        self.assertEqual(propriete.champs_manquants_pour_bail(), [])
        self.assertContains(self.client.get(reponse.url), 'value="820.00"')

    def test_nouveau_logement_proprietaire_existant(self):
        bailleur = self.creer_propriete().bailleur
        reponse = self.client.post(reverse("contrats:nouveau_logement"), {
            **self.LOGEMENT, "logement-proprietaire": bailleur.pk,
        })
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(Bailleur.objects.count(), 1)
        self.assertEqual(bailleur.proprietes.count(), 2)

    def test_formulaire_un_locataire_et_bouton_ajouter(self):
        reponse = self.client.get(reverse("contrats:creer"), {"propriete": self.creer_propriete().pk})
        self.assertContains(reponse, 'name="locataires-TOTAL_FORMS" value="1"')
        self.assertContains(reponse, 'name="locataires-0-nom"')
        self.assertNotContains(reponse, 'name="locataires-1-nom"')
        self.assertContains(reponse, 'name="locataires-__prefix__-nom"')
        self.assertContains(reponse, "+ Ajouter un locataire")
        self.assertContains(reponse, 'name="colocation"')

    def test_creation_colocation(self):
        propriete = self.creer_propriete()
        donnees = self.donnees_bail(propriete, ["Martin", "Bernard", "", "Petit", "Roux", "Faure"])
        donnees["colocation"] = "on"
        donnees["locataires-1-garants"] = "M. Jean Bernard, 5 rue Riquet, 31000 Toulouse"
        reponse = self.client.post(reverse("contrats:creer"), donnees)
        bail = Bail.objects.get()
        self.assertRedirects(reponse, bail.get_absolute_url(), fetch_redirect_response=False)
        self.assertTrue(bail.colocation)
        # La ligne retirée (vidée) est ignorée.
        self.assertEqual(bail.locataires.count(), 5)
        self.assertEqual(bail.locataires.get(nom="Bernard").garants, "M. Jean Bernard, 5 rue Riquet, 31000 Toulouse")
        # Étape « Bail » grisée (signature ouverte) : ses valeurs en lecture seule.
        page = self.client.get(bail.get_absolute_url(), {"etape": 4})
        self.assertContains(page, "Colocation, bail unique")
        self.assertContains(page, "Garant : M. Jean Bernard")

    def test_pas_plus_de_six_locataires(self):
        propriete = self.creer_propriete()
        reponse = self.client.post(reverse("contrats:creer"), self.donnees_bail(propriete, ["A", "B", "C", "D", "E", "F", "G"]))
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(Bail.objects.exists())

    def test_logement_deja_loue_sans_conge(self):
        """Pas de nouveau bail tant que le locataire en place n'a pas donné
        son préavis, ni avant son départ."""
        propriete = self.creer_propriete()
        en_place = self.creer_bail(propriete=propriete, date_effet=dt.date(2024, 9, 1))
        # Dès le choix du logement, renvoi vers le bail en cours.
        reponse = self.client.get(reverse("contrats:creer"), {"propriete": propriete.pk}, follow=True)
        self.assertRedirects(reponse, en_place.get_absolute_url())
        self.assertContains(reponse, "Ce logement est déjà loué (Paul MARTIN, bail du 01/09/2024)")
        # Et à l'enregistrement, si le formulaire est envoyé quand même.
        reponse = self.client.post(reverse("contrats:creer"), self.donnees_bail(propriete, ["Roux"]))
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("aucun congé", reponse.context["form"].errors["date_effet"][0])
        self.assertEqual(Bail.objects.count(), 1)

        # Congé reçu : le bail se rédige, mais pas avant le départ prévu.
        fin = reverse("contrats:fin", args=[en_place.pk])
        conge = {"conge_origine": "locataire", "conge_recu_le": "2026-10-05"}
        self.assertIn("date_depart_prevue", self.client.post(fin, conge).context["form"].errors)
        self.client.post(fin, {**conge, "date_depart_prevue": "2026-11-05"})
        self.assertContains(self.client.get(en_place.get_absolute_url()),
                            "congé du locataire reçu le 05/10/2026")
        self.assertEqual(self.client.get(reverse("contrats:creer"), {"propriete": propriete.pk}).status_code, 200)
        reponse = self.client.post(reverse("contrats:creer"), self.donnees_bail(propriete, ["Roux"]))  # effet le 01/11
        self.assertIn("prend fin le 05/11/2026", reponse.context["form"].errors["date_effet"][0])
        donnees_nouveau = self.donnees_bail(propriete, ["Roux"])
        donnees_nouveau["date_effet"] = "2026-11-06"
        self.client.post(reverse("contrats:creer"), donnees_nouveau)
        self.assertEqual(Bail.objects.count(), 2)
        # Le bail en place (non signé) reste modifiable malgré le suivant.
        donnees = self.donnees_bail(propriete, ["Martin"])
        donnees.update({"locataires-INITIAL_FORMS": "1", "locataires-0-id": en_place.locataires.get().pk,
                        "date_effet": "2024-09-01"})
        self.assertRedirects(self.client.post(en_place.get_absolute_url(), donnees), f"{en_place.get_absolute_url()}#etape-7")

    def test_parcours_depart_du_locataire(self):
        """De « loué » à « libre » depuis la fiche du logement, sans rouvrir
        le bail : congé proposé d'avance, puis départ effectif."""
        aujourd_hui = timezone.localdate()
        propriete = self.creer_propriete(zone_tendue=False)
        bail = self.creer_bail(propriete=propriete, date_effet=dt.date(2024, 9, 1), date_signature=dt.date(2024, 8, 20))
        self.assertEqual(propriete.situation.code, "loue")
        page = self.client.get(propriete.get_absolute_url())
        self.assertContains(page, '<span class="pastille pastille-loue">Loué</span>')
        self.assertContains(page, f'href="{reverse("contrats:fin", args=[bail.pk])}">Départ du locataire</a>')
        self.assertNotContains(page, "Enregistrer une candidature")
        # Logement nu hors zone tendue : trois mois de préavis proposés.
        page = self.client.get(reverse("contrats:fin", args=[bail.pk]))
        initial = page.context["form"].initial
        self.assertEqual((initial["conge_recu_le"], initial["date_depart_prevue"]),
                         (aujourd_hui, ajouter_mois(aujourd_hui, 3)))
        depart = aujourd_hui + dt.timedelta(days=30)
        self.assertEqual(initial["conge_origine"], "locataire")
        donnees = {"conge_recu_le": aujourd_hui.isoformat(), "date_depart_prevue": depart.isoformat()}
        reponse = self.client.post(reverse("contrats:fin", args=[bail.pk]), donnees)
        self.assertIn("conge_origine", reponse.context["form"].errors)
        # Le congé enregistré ouvre la mise en location du logement.
        reponse = self.client.post(reverse("contrats:fin", args=[bail.pk]), {**donnees, "conge_origine": "bailleur"})
        self.assertRedirects(reponse, reverse("proprietes:mettre_en_location", args=[propriete.pk]))
        propriete = Propriete.objects.get(pk=propriete.pk)
        self.assertEqual((propriete.situation.code, propriete.situation.libelle),
                         ("preavis", f"Préavis, libre le {depart:%d/%m/%Y}"))
        page = self.client.get(propriete.get_absolute_url())
        self.assertContains(page, "congé du bailleur")
        self.assertContains(page, "Confirmer le départ du locataire")
        self.assertNotContains(page, "Enregistrer une candidature")
        page = self.client.get(reverse("proprietes:mettre_en_location", args=[propriete.pk]))
        self.assertEqual(page.context["form"].initial["disponible_le"], depart + dt.timedelta(days=1))
        self.assertContains(page, "Aucun mandat de gestion en cours")
        Mandat.depuis_propriete(propriete).save()
        reponse = self.client.post(reverse("proprietes:mettre_en_location", args=[propriete.pk]), {
            "disponible_le": (depart + dt.timedelta(days=1)).isoformat(), "dernier_loyer": "720", "charges": "50"})
        self.assertRedirects(reponse, propriete.get_absolute_url())
        propriete = Propriete.objects.get(pk=propriete.pk)
        self.assertEqual((propriete.a_louer, propriete.mise_en_location_le, propriete.dernier_loyer),
                         (True, aujourd_hui, Decimal("720")))
        page = self.client.get(propriete.get_absolute_url())
        self.assertEqual(page.context["location"].courante.libelle, "Candidatures")
        self.assertContains(page, "pastille-a_louer")
        self.assertContains(page, "Enregistrer une candidature")
        self.assertEqual([b.pk for b in self.client.get(reverse("proprietes:liste"), {"situation": "a_louer"}).context["page"]],
                         [propriete.pk])
        # Le bail du nouveau locataire referme la mise en location.
        nouveau = self.donnees_bail(propriete, ["Roux"])
        nouveau["date_effet"] = (depart + dt.timedelta(days=1)).isoformat()
        self.client.post(reverse("contrats:creer"), nouveau)
        self.assertEqual(propriete.baux.count(), 2)
        self.assertFalse(Propriete.objects.get(pk=propriete.pk).a_louer)
        propriete.baux.exclude(pk=bail.pk).delete()
        # Départ effectif : le logement redevient libre.
        hier = aujourd_hui - dt.timedelta(days=1)
        reponse = self.client.post(reverse("contrats:fin", args=[bail.pk]), {
            "conge_origine": "bailleur", "conge_recu_le": (hier - dt.timedelta(days=30)).isoformat(),
            "date_depart_prevue": hier.isoformat(), "date_fin_effective": hier.isoformat()}, follow=True)
        self.assertContains(reponse, "logement libre depuis le")
        self.assertEqual(Propriete.objects.get(pk=propriete.pk).situation.code, "libre")
        # Un contrat terminé est réservé à un administrateur ; les règles de dates restent vérifiées.
        admin = User.objects.create_user("admin-fin")
        attribuer_role(admin, ADMINISTRATEURS)
        self.client.force_login(admin)
        reponse = self.client.post(reverse("contrats:fin", args=[bail.pk]), {"conge_recu_le": aujourd_hui.isoformat(), "motif": "Vérification des dates"})
        self.assertIn("date_depart_prevue", reponse.context["form"].errors)
        reponse = self.client.post(reverse("contrats:fin", args=[bail.pk]), {"date_fin_effective": "2020-01-01", "motif": "Vérification des dates"})
        self.assertIn("date_fin_effective", reponse.context["form"].errors)

    def test_preavis_d_un_mois_en_meuble_ou_zone_tendue(self):
        self.assertEqual(self.creer_bail(propriete=self.creer_propriete(zone_tendue=False)).mois_preavis, 3)
        self.assertEqual(self.creer_bail(propriete=self.creer_propriete(zone_tendue=True)).mois_preavis, 1)
        self.assertEqual(self.creer_bail(propriete=self.creer_propriete(zone_tendue=False), type=Bail.Type.MEUBLE).mois_preavis, 1)

    def test_fiche_du_logement_selon_l_etape(self):
        propriete = self.creer_propriete()
        self.assertEqual(propriete.situation.code, "sans_bail")
        page = self.client.get(propriete.get_absolute_url())
        self.assertContains(page, "Aucun bail enregistré")
        self.assertContains(page, "Créer le mandat")
        self.assertEqual(page.context["location"].courante.libelle, "Mise en location")
        Mandat.depuis_propriete(propriete).save()
        page = self.client.get(propriete.get_absolute_url())
        # Le mandat n'est pas une étape de la location : seule la mise en
        # location la fait avancer.
        self.assertEqual(page.context["location"].courante.libelle, "Mise en location")
        self.assertContains(page, f'href="{reverse("proprietes:mettre_en_location", args=[propriete.pk])}">Mettre en location</a>')
        # Les autres rubriques de la fiche sont dans leurs onglets.
        self.assertNotContains(page, "<h2>Description</h2>")
        self.assertContains(self.client.get(propriete.get_absolute_url(), {"onglet": "informations"}), "<h2>Description</h2>")
        # Mandats et baux sont réunis dans l'onglet Contrats.
        page = self.client.get(propriete.get_absolute_url(), {"onglet": "contrats"})
        self.assertContains(page, "Mandat n° ")
        self.assertContains(page, "pastille-type-mandat")
        self.assertNotContains(page, "pastille-type-bail")
        self.assertEqual(self.client.get(propriete.get_absolute_url(), {"onglet": "mandats"}).context["onglet"], "contrats")
        # Bail rédigé pour une entrée à venir : il reste à le faire signer.
        bail = self.creer_bail(propriete=propriete, date_effet=timezone.localdate() + dt.timedelta(days=20))
        page = self.client.get(propriete.get_absolute_url())
        self.assertEqual(page.context["location"].courante.libelle, "Signature")
        self.assertContains(page, "Faire signer le bail")
        self.assertContains(page, "Prochain locataire")
        bail.date_signature = timezone.localdate()
        bail.save()
        self.assertEqual(self.client.get(propriete.get_absolute_url()).context["location"].courante.libelle, "Entrée dans les lieux")
        page = self.client.get(propriete.get_absolute_url(), {"onglet": "contrats"})
        self.assertContains(page, "Paul MARTIN")
        self.assertEqual([contrat.TYPE for contrat in page.context["contrats"]], ["Bail", "Mandat"])

    def test_liste_des_proprietes_par_situation(self):
        aujourd_hui = timezone.localdate()
        loue = self.creer_propriete(adresse="1 rue Louee")
        self.creer_bail(propriete=loue, date_effet=dt.date(2024, 9, 1))
        preavis = self.creer_propriete(adresse="2 rue Preavis")
        self.creer_bail(propriete=preavis, date_effet=dt.date(2024, 9, 1), conge_recu_le=aujourd_hui,
                        date_depart_prevue=aujourd_hui + dt.timedelta(days=30))
        libre = self.creer_propriete(adresse="3 rue Libre")
        self.creer_bail(propriete=libre, date_effet=dt.date(2022, 1, 1), date_fin_effective=dt.date(2025, 1, 1))
        sans = self.creer_propriete(adresse="4 rue Sans Bail")
        for situation, attendus in (("loue", [loue]), ("preavis", [preavis]), ("libre", [libre, sans])):
            with self.subTest(situation=situation):
                page = self.client.get(reverse("proprietes:liste"), {"situation": situation})
                self.assertEqual(sorted(b.pk for b in page.context["page"]), sorted(b.pk for b in attendus))
        page = self.client.get(reverse("proprietes:liste"))
        self.assertContains(page, "pastille-loue")
        self.assertContains(page, "pastille-preavis")
        self.assertContains(page, "Aucun bail enregistré")

    def test_accueil_locations_en_cours(self):
        aujourd_hui = timezone.localdate()
        tranquille = self.creer_propriete(adresse="1 rue Tranquille")
        self.creer_bail(propriete=tranquille, date_effet=dt.date(2024, 9, 1), date_signature=dt.date(2024, 8, 20))
        a_signer = self.creer_propriete(adresse="2 rue A Signer")
        bail = self.creer_bail(propriete=a_signer, date_effet=aujourd_hui + dt.timedelta(days=10))
        a_relouer = self.creer_propriete(adresse="3 rue A Relouer")
        self.creer_bail(propriete=a_relouer, date_effet=dt.date(2023, 1, 1), date_signature=dt.date(2022, 12, 20),
                        conge_recu_le=aujourd_hui, date_depart_prevue=aujourd_hui + dt.timedelta(days=30))
        page = self.client.get(reverse("accueil"))
        lignes = {l["propriete"].pk: l for l in page.context["locations"]}
        self.assertEqual(set(lignes), {a_signer.pk, a_relouer.pk})
        self.assertEqual((lignes[a_signer.pk]["action"], lignes[a_signer.pk]["lien"]),
                         ("Ouvrir le bail", bail.get_absolute_url()))
        self.assertEqual((lignes[a_relouer.pk]["etape"], lignes[a_relouer.pk]["action"]),
                         ("Remettre en location", "Mettre en location"))
        Propriete.objects.filter(pk=a_relouer.pk).update(mise_en_location_le=aujourd_hui, disponible_le=aujourd_hui)
        page = self.client.get(reverse("accueil"))
        ligne = next(l for l in page.context["locations"] if l["propriete"].pk == a_relouer.pk)
        self.assertEqual((ligne["etape"], ligne["action"]), ("Enregistrer une candidature", "Enregistrer une candidature"))
        # Le congé reçu donne la date à laquelle le logement sera libre.
        self.assertEqual(ligne["detail"], f"libre le {aujourd_hui + dt.timedelta(days=30):%d/%m/%Y}")

    def test_liste_des_contrats_baux_et_mandats(self):
        """La rubrique Contrats réunit les deux sous-types, filtrables."""
        propriete = self.creer_propriete()
        mandat = Mandat.depuis_propriete(propriete)
        mandat.date_signature = dt.date(2024, 8, 1)
        mandat.save()
        bail = self.creer_bail(propriete=propriete, date_effet=dt.date(2024, 9, 1), date_signature=dt.date(2024, 8, 20))
        ancien = self.creer_bail(propriete=self.creer_propriete(adresse="3 rue Ancienne"), date_effet=dt.date(2020, 1, 1),
                                 date_fin_effective=dt.date(2023, 1, 1))
        page = self.client.get(reverse("contrats:liste"))
        self.assertEqual(list(page.context["page"]), [bail, mandat, ancien])
        self.assertContains(page, "pastille-type-bail")
        self.assertContains(page, f"mandat n° {mandat.numero}")
        self.assertContains(page, "Registre des mandats")
        self.assertEqual(list(self.client.get(reverse("contrats:liste"), {"type": "mandat"}).context["page"]), [mandat])
        self.assertEqual(list(self.client.get(reverse("contrats:liste"), {"type": "bail"}).context["page"]), [bail, ancien])
        self.assertEqual(list(self.client.get(reverse("contrats:liste"), {"etat": "termines"}).context["page"]), [ancien])
        self.assertEqual(list(self.client.get(reverse("contrats:liste"), {"q": "durand"}).context["page"]), [mandat])
        self.assertEqual(list(self.client.get(reverse("contrats:liste"), {"q": "ancienne"}).context["page"]), [ancien])
        # Interface commune aux deux sous-types.
        self.assertEqual((bail.TYPE, bail.parties, bail.debut, bail.etat), ("Bail", "Paul MARTIN", dt.date(2024, 9, 1), ("loue", "En cours")))
        self.assertEqual((mandat.TYPE, mandat.debut, mandat.echeance), ("Mandat", dt.date(2024, 8, 1), (None, "")))
        self.assertEqual(ancien.etat, ("sans_bail", "Terminé"))
        # Les pages des mandats se rangent sous la rubrique Contrats.
        self.assertContains(self.client.get(reverse("mandats:registre")), 'aria-current="page"')

    def test_logement_libere(self):
        propriete = self.creer_propriete()
        self.creer_bail(propriete=propriete, date_effet=dt.date(2024, 9, 1), date_fin_effective=dt.date(2026, 10, 15))
        self.assertEqual(self.client.get(reverse("contrats:creer"), {"propriete": propriete.pk}).status_code, 200)
        self.client.post(reverse("contrats:creer"), self.donnees_bail(propriete, ["Roux"]))
        self.assertEqual(Bail.objects.count(), 2)

    def test_nouveau_logement_sans_proprietaire_refuse(self):
        reponse = self.client.post(reverse("contrats:nouveau_logement"), self.LOGEMENT)
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(Propriete.objects.exists())
        self.assertContains(reponse, "Ce champ est obligatoire")

    def test_sans_locataire_refuse(self):
        propriete = self.creer_propriete()
        reponse = self.client.post(reverse("contrats:creer"), {
            "propriete": propriete.pk, "type": "nu", "date_effet": "2026-11-01", "lieu_signature": "Toulouse",
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
        self.assertIn("MARTIN Paul", texte_docx(self.client.get(reverse("contrats:word", args=[bail.pk])).content))
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
            self.assertEqual(self.client.get(reverse("contrats:pdf", args=[bail.pk])).content, b"%PDF-1.7")
        for url in [reverse("contrats:liste"), reverse("contrats:bail", args=[bail.pk]),
                    reverse("proprietes:propriete", args=[bail.propriete.pk]),
                    reverse("contrats:nouveau_logement"), reverse("accueil")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        # L'accueil donne accès au PDF des derniers baux.
        self.assertContains(self.client.get(reverse("accueil")), reverse("contrats:pdf", args=[bail.pk]))


@override_settings(MFA_OBLIGATOIRE=False)
class EtapesTests(Donnees, TestCase):
    """La page d'un bail, façon PLM : la frise entre ses flèches ‹ ›, une
    section par étape, l'étape en cours ouverte avec ses champs, les étapes
    faites grisées, avec un crayon tant qu'elles se modifient et un cadenas
    une fois le bail signé."""

    def setUp(self):
        self.client.force_login(creer_gestionnaire("gestion"))
        self.aujourd_hui = timezone.localdate()

    def bail_avec_mandat(self, **autres):
        propriete = self.creer_propriete()
        mandat = Mandat.depuis_propriete(propriete)
        mandat.save()
        return self.creer_bail(propriete, mandat=mandat, **autres)

    def etapes(self, bail, **parametres):
        page = self.client.get(bail.get_absolute_url(), parametres)
        return page, {section["numero"]: section["mode"] for section in page.context["sections"]}

    def dossier_temporaire(self):
        import shutil
        import tempfile

        dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dossier, ignore_errors=True)
        return dossier

    def crayons(self, page):
        return [section["numero"] for section in page.context["sections"] if section["crayon"]]

    def fleches(self, page):
        """Les étapes où mènent ‹ et ›, None pour une flèche grisée."""
        return tuple(fleche and fleche["numero"] for fleche in (page.context["precedente"], page.context["suivante"]))

    def url_etapes(self, bail, suite=""):
        return bail.get_absolute_url() + suite

    def donnees_modifiees(self, bail, **autres):
        donnees = self.donnees_bail(bail.propriete, ["Martin"])
        donnees.update({"locataires-INITIAL_FORMS": "1", "locataires-0-id": bail.locataires.get().pk,
                        "date_effet": bail.date_effet.isoformat(), "mandat": bail.mandat_id, **autres})
        return donnees

    def test_ancienne_adresse_modifier(self):
        bail = self.creer_bail()
        self.assertRedirects(self.client.get(reverse("contrats:modifier", args=[bail.pk]), {"etape": 3}),
                             self.url_etapes(bail, "?etape=3"))

    def test_bail_a_signer(self):
        bail = self.bail_avec_mandat(date_effet=self.aujourd_hui + dt.timedelta(days=30))
        # Ouvrir un bail mène droit à ses étapes, la signature ouverte.
        page, modes = self.etapes(bail)
        self.assertEqual(modes, {1: "grisee", 2: "grisee", 3: "grisee", 4: "ouverte", 5: "a-venir", 6: "a-venir", 7: "a-venir"})
        self.assertContains(page, reverse("contrats:deposer_signe", args=[bail.pk]))
        self.assertContains(page, "Enregistrer et passer à l'entrée ›")
        self.assertNotContains(page, "Retirer la signature")
        # Pas encore signé, le bail garde son crayon, et ‹ le rouvre.
        self.assertEqual(self.crayons(page), [3])
        self.assertEqual(self.fleches(page), (3, None))
        self.assertContains(page, 'class="crayon" href="?etape=3#etape-3"')
        # Chaque point de la frise mène à son étape.
        self.assertContains(page, self.url_etapes(bail, "?etape=4#etape-4"))
        # Le crayon : les champs du bail s'ouvrent, la signature reste en cours et › y ramène.
        page, modes = self.etapes(bail, etape=3)
        self.assertEqual((modes[3], modes[4]), ("ouverte", "grisee"))
        self.assertEqual(self.fleches(page), (None, 4))
        self.assertEqual(self.crayons(page), [4])
        self.assertContains(page, ">Modification</span>")
        self.assertContains(page, ">Annuler</a>")
        # Enregistré, on revient à l'étape en cours.
        reponse = self.client.post(self.url_etapes(bail), self.donnees_modifiees(bail, loyer="720"))
        self.assertRedirects(reponse, self.url_etapes(bail, "#etape-4"))
        bail.refresh_from_db()
        self.assertEqual(bail.loyer, Decimal("720"))
        # La signature enregistrée, l'entrée s'ouvre.
        self.client.post(reverse("documents:preparer", args=["bail", bail.pk]))
        reponse = self.client.post(reverse("contrats:deposer_signe", args=[bail.pk]),
                                   {"date_signature": self.aujourd_hui.isoformat()})
        self.assertRedirects(reponse, self.url_etapes(bail, "#etape-5"))
        _, modes = self.etapes(bail)
        self.assertEqual((modes[4], modes[5]), ("grisee", "ouverte"))

    def test_bail_signe_verrouille(self):
        """Signé, le bail ne se modifie plus : ‹ rouvre la signature, qui se
        retire tant que le locataire n'est pas entré."""
        bail = self.bail_avec_mandat(date_effet=self.aujourd_hui + dt.timedelta(days=30), date_signature=self.aujourd_hui)
        page, modes = self.etapes(bail, etape=3)
        self.assertEqual((modes[3], modes[5]), ("grisee", "ouverte"))
        self.assertContains(page, "verrouillé, bail signé")
        self.assertNotContains(page, ">Corriger</a>")
        self.assertEqual(self.crayons(page), [4])
        self.assertEqual(self.fleches(page), (4, None))
        reponse = self.client.post(self.url_etapes(bail), self.donnees_modifiees(bail, loyer="900"), follow=True)
        self.assertContains(reponse, "Ce bail est signé : pour le corriger, revenir d&#x27;abord à la signature.")
        bail.refresh_from_db()
        self.assertEqual(bail.loyer, Decimal("700"))
        # ‹ : la signature s'ouvre, et propose de se retirer.
        page, modes = self.etapes(bail, etape=4)
        self.assertEqual((modes[4], modes[5]), ("ouverte", "grisee"))
        self.assertContains(page, "Retirer la signature…")
        self.assertEqual(self.fleches(page), (None, 5))
        # La date est retirée (le scan resterait) et le bail se rouvre.
        reponse = self.client.post(reverse("contrats:retirer_signature", args=[bail.pk]))
        self.assertRedirects(reponse, self.url_etapes(bail, "?etape=3#etape-3"))
        bail.refresh_from_db()
        self.assertFalse(bail.signe)
        _, modes = self.etapes(bail, etape=3)
        self.assertEqual(modes[3], "ouverte")

    def test_apres_l_entree(self):
        """Le locataire entré, plus rien ne se rouvre en arrière ; seul un
        administrateur peut encore corriger le bail."""
        from comptes.roles import ADMINISTRATEURS, attribuer_role

        bail = self.bail_avec_mandat(date_effet=dt.date(2025, 2, 1), date_signature=dt.date(2025, 1, 20))
        page, modes = self.etapes(bail)
        self.assertEqual(modes[6], "ouverte")
        self.assertEqual(self.crayons(page), [])
        self.assertEqual(self.fleches(page), (None, None))
        self.assertContains(page, 'class="fleche-frise" aria-disabled="true"', count=2)
        self.assertContains(page, 'class="verrou"', count=2)
        self.client.post(reverse("contrats:retirer_signature", args=[bail.pk]))
        bail.refresh_from_db()
        self.assertTrue(bail.signe)
        _, modes = self.etapes(bail, etape=4)
        self.assertEqual(modes[4], "grisee")
        # « Corriger » : le bail s'ouvre, les autres étapes restent où elles sont.
        admin = User.objects.create_user("admin")
        attribuer_role(admin, ADMINISTRATEURS)
        self.client.force_login(admin)
        self.assertContains(self.client.get(self.url_etapes(bail)), ">Corriger</a>", count=1)
        page, modes = self.etapes(bail, etape=3)
        self.assertEqual(modes, {1: "grisee", 2: "grisee", 3: "ouverte", 4: "grisee", 5: "grisee", 6: "grisee", 7: "a-venir"})
        self.assertContains(page, "Enregistrer la correction")
        reponse = self.client.post(self.url_etapes(bail), {**self.donnees_modifiees(bail, lieu_signature="Muret"), "motif": "Correction du lieu de signature"})
        self.assertRedirects(reponse, self.url_etapes(bail, "#etape-6"))
        bail.refresh_from_db()
        self.assertEqual((bail.lieu_signature, bail.signe), ("Muret", True))

    def test_location_en_cours_et_conge(self):
        bail = self.bail_avec_mandat(date_effet=dt.date(2025, 2, 1), date_signature=dt.date(2025, 1, 20))
        page, modes = self.etapes(bail)
        self.assertEqual(modes[6], "ouverte")
        self.assertContains(page, "Un congé est arrivé ›")
        # › Un congé est arrivé : la sortie s'ouvre, avec le préavis proposé ; ‹ ramène à la location.
        page, modes = self.etapes(bail, etape=7)
        self.assertEqual((modes[6], modes[7]), ("grisee", "ouverte"))
        self.assertEqual(self.fleches(page), (6, None))
        self.assertEqual(self.crayons(page), [6])
        depart = ajouter_mois(self.aujourd_hui, bail.mois_preavis)
        self.assertEqual(page.context["form_fin"]["date_depart_prevue"].value(), depart)
        self.client.post(reverse("contrats:fin", args=[bail.pk]), {
            "conge_origine": "locataire", "conge_recu_le": self.aujourd_hui.isoformat(),
            "date_depart_prevue": depart.isoformat(), "etapes": "1",
        })
        # Le congé fait, la petite frise de la sortie passe à l'état des lieux ;
        # le congé se rouvre d'un clic, avec son annulation.
        page, modes = self.etapes(bail)
        self.assertEqual((modes[7], page.context["sortie"]["ouverte"]), ("ouverte", 2))
        self.assertContains(page, "Clés rendues et EDL le")
        page, _ = self.etapes(bail, etape=7, sous=1)
        self.assertEqual(page.context["sortie"]["ouverte"], 1)
        self.assertContains(page, "Annuler le congé…")
        # Annuler le congé : la location continue.
        self.assertRedirects(self.client.post(reverse("contrats:annuler_conge", args=[bail.pk])), self.url_etapes(bail, "#etape-6"))
        bail.refresh_from_db()
        self.assertEqual((bail.conge_origine, bail.conge_recu_le, bail.date_depart_prevue), ("", None, None))

    def test_etat_des_lieux_d_entree(self):
        """Un bail signé qui prend effet après la mise en service attend son
        état des lieux d'entrée, même la date d'effet passée."""
        debut = max(SUIVI_EDL_DEPUIS, self.aujourd_hui - dt.timedelta(days=3))
        bail = self.bail_avec_mandat(date_effet=debut, date_signature=debut - dt.timedelta(days=10))
        page, modes = self.etapes(bail)
        self.assertEqual(modes[5], "ouverte")
        self.assertContains(page, "Enregistrer et passer à la location ›")
        pdf = SimpleUploadedFile("edl.pdf", b"%PDF-1.4 edl", content_type="application/pdf")
        faux = SimpleUploadedFile("edl.pdf", b"pas un pdf", content_type="application/pdf")
        url = reverse("contrats:entree", args=[bail.pk])
        with override_settings(MEDIA_ROOT=self.dossier_temporaire()):
            self.client.post(url, {"edl_entree_le": debut.isoformat(), "edl_entree": faux})
            bail.refresh_from_db()
            self.assertIsNone(bail.edl_entree_le)
            demain = self.aujourd_hui + dt.timedelta(days=1)
            self.client.post(url, {"edl_entree_le": demain.isoformat(), "edl_entree": pdf})
            bail.refresh_from_db()
            self.assertIsNone(bail.edl_entree_le)
            pdf.seek(0)
            self.client.post(url, {"edl_entree_le": debut.isoformat(), "edl_entree": pdf, "cles_remises": "3"})
            bail.refresh_from_db()
            self.assertEqual((bail.edl_entree_le, bail.cles_remises), (debut, 3))
            page, modes = self.etapes(bail)
            self.assertEqual((modes[5], modes[6]), ("grisee", "ouverte"))
            self.assertIn(5, self.crayons(page))
            self.assertEqual(self.client.get(reverse("contrats:fichier_edl", args=[bail.pk, "edl_entree"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("contrats:fichier_edl", args=[bail.pk, "fichier_signe"])).status_code, 404)

    def test_sortie_etat_des_lieux_puis_restitution(self):
        """Clés rendues : le logement est libre, mais le bail ne se termine
        qu'à la restitution du dépôt, sous un mois (conforme) ou deux."""
        bail = self.bail_avec_mandat(
            date_effet=dt.date(2025, 2, 1), date_signature=dt.date(2025, 1, 20), depot_garantie=Decimal("800"),
            conge_origine="locataire", conge_recu_le=self.aujourd_hui - dt.timedelta(days=40),
            date_depart_prevue=self.aujourd_hui - dt.timedelta(days=1),
        )
        cles = max(SUIVI_EDL_DEPUIS, self.aujourd_hui - dt.timedelta(days=1))
        Propriete.objects.filter(pk=bail.propriete_id).update(mise_en_location_le=self.aujourd_hui)
        self.client.post(reverse("contrats:edl_sortie", args=[bail.pk]), {
            "date_fin_effective": cles.isoformat(), "edl_sortie_conforme": "False",
        })
        bail.refresh_from_db()
        self.assertEqual(bail.date_fin_effective, cles)
        self.assertFalse(bail.en_cours)
        self.assertTrue(bail.restitution_attendue)
        self.assertEqual(bail.restitution_avant, ajouter_mois(cles, 2))
        from core.taches import taches

        titres = {tache.titre: tache for tache in taches([])}
        self.assertEqual(next(t for t in titres.values() if t.titre.startswith("Dépôt de garantie à restituer")).detail, f"avant le {ajouter_mois(cles, 2):%d/%m/%Y}")
        page, modes = self.etapes(bail)
        self.assertEqual((modes[7], page.context["sortie"]["ouverte"]), ("ouverte", 3))
        self.assertContains(page, "Enregistrer et terminer le bail")
        self.assertEqual(page.context["processus"].termine, False)
        url = reverse("contrats:restitution", args=[bail.pk])
        jour = self.aujourd_hui.isoformat()
        # Des retenues sans motif, ou plus fortes que le dépôt, sont refusées.
        self.client.post(url, {"depot_retenues": "100", "depot_motif": "", "depot_restitue_le": jour})
        self.client.post(url, {"depot_retenues": "900", "depot_motif": "Travaux", "depot_restitue_le": jour})
        bail.refresh_from_db()
        self.assertIsNone(bail.depot_restitue_le)
        self.client.post(url, {"depot_retenues": "150", "depot_motif": "Plan de travail brûlé", "depot_restitue_le": jour})
        bail.refresh_from_db()
        self.assertEqual((bail.depot_a_rendre, bail.termine), (Decimal("650"), True))
        page, modes = self.etapes(bail, etape=7)
        self.assertEqual(set(modes.values()), {"grisee"})
        self.assertContains(page, "dépôt restitué le")
        # Terminé : un gestionnaire ne corrige plus la restitution.
        self.client.post(url, {"depot_retenues": "0", "depot_restitue_le": jour})
        bail.refresh_from_db()
        self.assertEqual(bail.depot_retenues, Decimal("150"))

    def test_sortie_ancienne_sans_restitution(self):
        """Un départ antérieur au suivi des dépôts termine le bail comme avant."""
        bail = self.bail_avec_mandat(date_effet=dt.date(2025, 2, 1), date_signature=dt.date(2025, 1, 20),
                                     depot_garantie=Decimal("800"), date_fin_effective=dt.date(2026, 1, 31))
        self.assertTrue(bail.termine)
        self.assertFalse(bail.edl_entree_attendu)

    def test_bail_termine(self):
        bail = self.bail_avec_mandat(date_effet=dt.date(2025, 2, 1), date_signature=dt.date(2025, 1, 20),
                                     date_fin_effective=dt.date(2026, 1, 31))
        page, modes = self.etapes(bail, etape=7)
        self.assertEqual(set(modes.values()), {"grisee"})
        self.assertEqual((self.crayons(page), self.fleches(page)), ([], (None, None)))
        self.assertContains(page, "Terminé")

    def test_logement_plus_gere(self):
        """Un logement décoché « Actif » (plus géré par l'agence) reste
        affiché dans l'étape « Bail », qui s'enregistre toujours."""
        bail = self.bail_avec_mandat(date_effet=self.aujourd_hui + dt.timedelta(days=30))
        Propriete.objects.filter(pk=bail.propriete_id).update(actif=False)
        page, modes = self.etapes(bail, etape=3)
        self.assertEqual(modes[3], "ouverte")
        self.assertIn(f'<option value="{bail.propriete_id}" selected>', str(page.context["form"]["propriete"]))
        reponse = self.client.post(self.url_etapes(bail), self.donnees_modifiees(bail, loyer="710"))
        self.assertRedirects(reponse, self.url_etapes(bail, "#etape-4"))
        bail.refresh_from_db()
        self.assertEqual(bail.loyer, Decimal("710"))


class InterdictionsTests(Donnees, TestCase):
    """Ce que la loi interdit bloque la préparation, le téléchargement et la
    signature du bail, au lieu d'un simple bandeau."""

    def setUp(self):
        self.client.force_login(creer_gestionnaire("gestion"))

    def verifier_bloque(self, bail, message):
        preparer = self.client.post(reverse("documents:preparer", args=["bail", bail.pk]), follow=True)
        self.assertContains(preparer, message)
        self.assertFalse(bail.versions_document.exists())
        brouillon = self.client.get(reverse("contrats:word", args=[bail.pk]), follow=True)
        self.assertContains(brouillon, message)
        from django.core.files.uploadedfile import SimpleUploadedFile
        scan = SimpleUploadedFile("bail.pdf", b"%PDF-1.7", content_type="application/pdf")
        self.client.post(reverse("contrats:deposer_signe", args=[bail.pk]),
                         {"date_signature": "2026-10-10", "fichier_signe": scan})
        bail.refresh_from_db()
        self.assertFalse(bail.signe)

    def test_logement_classe_g(self):
        bail = self.creer_bail(self.creer_propriete(classe_dpe="G"))
        self.verifier_bloque(bail, "Logement classé G")

    def test_bien_qui_n_est_pas_d_habitation(self):
        bail = self.creer_bail(self.creer_propriete(usage="commercial"))
        self.verifier_bloque(bail, "Ce bien est un local commercial")

    def test_depot_de_garantie_au_dela_du_plafond(self):
        bail = self.creer_bail(loyer=Decimal("700"), depot_garantie=Decimal("1400"))
        self.verifier_bloque(bail, "Dépôt de garantie supérieur à 1 mois")

    def test_bail_conforme_se_prepare(self):
        bail = self.creer_bail(loyer=Decimal("700"), depot_garantie=Decimal("700"))
        self.assertEqual(bail.interdictions, [])
        self.client.post(reverse("documents:preparer", args=["bail", bail.pk]))
        self.assertTrue(bail.versions_document.filter(nature="preparation").exists())

    def test_clause_resolutoire_a_six_semaines(self):
        texte = generation.texte_docx(generation.chemin_modele("bail_habitation.docx").read_bytes())
        self.assertIn("six semaines après un commandement de payer", texte)
        self.assertEqual(generation.defauts_modele("bail_habitation.docx"), [])

    def test_modele_avec_l_ancienne_clause(self):
        """Un modèle déposé qui garde « deux mois » est refusé, et s'il est
        déjà en service, il bloque la préparation."""
        import shutil
        import tempfile
        import zipfile

        from documents.views import verifier_modele

        source = generation.chemin_modele("bail_habitation.docx").read_bytes()
        sortie = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(source)) as entree, zipfile.ZipFile(sortie, "w") as archive:
            for element in entree.infolist():
                contenu = entree.read(element.filename)
                if element.filename == "word/document.xml":
                    contenu = contenu.replace("six semaines après un commandement".encode(),
                                              "deux mois après un commandement".encode())
                archive.writestr(element, contenu)
        ancien = sortie.getvalue()
        self.assertIn("six semaines", verifier_modele("bail_habitation.docx", ancien))
        dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dossier)
        with self.settings(MEDIA_ROOT=dossier):
            (generation.Path(dossier) / "modeles").mkdir()
            (generation.Path(dossier) / "modeles" / "bail_habitation.docx").write_bytes(ancien)
            bail = self.creer_bail(loyer=Decimal("700"), depot_garantie=Decimal("700"))
            reponse = self.client.post(reverse("documents:preparer", args=["bail", bail.pk]), follow=True)
            self.assertContains(reponse, "c&#x27;est six semaines")
            self.assertFalse(bail.versions_document.exists())
