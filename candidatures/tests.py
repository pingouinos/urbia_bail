import datetime as dt
import io
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from baux.models import Bail
from baux.tests import Donnees as DonneesBaux
from mandats.models import Mandat

from .models import Candidat, Candidature, valider_lien_dossierfacile

LIEN = "https://locataire.dossierfacile.logement.gouv.fr/file/3f2a"


class Donnees(DonneesBaux):
    def creer_candidature(self, bien=None, candidats=(("Martin", 1500), ("Petit", 1000)), **autres):
        candidature = Candidature.objects.create(
            bien=bien or self.creer_bien(), lien_dossierfacile=LIEN, date_entree_souhaitee=dt.date(2026, 11, 1),
            **autres,
        )
        for nom, revenus in candidats:
            Candidat.objects.create(
                candidature=candidature, civilite="Mme", nom=nom, prenom="Julie",
                date_naissance=dt.date(1996, 3, 8), lieu_naissance="Albi", email=f"{nom.lower()}@exemple.fr",
                contrat=Candidat.Contrat.CDI, revenus_mensuels=Decimal(revenus),
            )
        return candidature

    def vieillir(self, candidature, jours, champ="decide_le"):
        Candidature.objects.filter(pk=candidature.pk).update(**{champ: timezone.now() - dt.timedelta(days=jours)})


class CandidatureTests(Donnees, TestCase):
    def test_lien_dossierfacile(self):
        for lien in [LIEN, "https://dossierfacile.fr/file/abc"]:
            valider_lien_dossierfacile(lien)
        for lien in ["https://exemple.fr/dossierfacile.fr", "https://dossierfacile.fr.exemple.com/x"]:
            with self.subTest(lien=lien), self.assertRaises(ValidationError):
                valider_lien_dossierfacile(lien)

    def test_revenus_et_taux_effort(self):
        candidature = self.creer_candidature()
        # Loyer 700 € + 50 € de charges pour 2 500 € de revenus.
        self.assertEqual((candidature.revenus, candidature.taux_effort), (Decimal("2500"), 30))
        self.assertIsNone(self.creer_candidature(candidats=()).taux_effort)

    def test_caution_et_assurance_loyers_impayes(self):
        bien = self.creer_bien()
        Mandat.depuis_bien(bien).save()
        Mandat.objects.update(assurance_loyers_impayes=True)
        candidature = self.creer_candidature(bien, garantie=Candidature.Garantie.PERSONNE)
        self.assertTrue(any("art. 22-1" in alerte for alerte in candidature.alertes))
        candidature.candidats.update(contrat=Candidat.Contrat.ETUDIANT)
        self.assertEqual(candidature.alertes, [])

    def test_decision(self):
        utilisateur = User.objects.create_user("gestion")
        candidature = self.creer_candidature()
        candidature.decider(Candidature.Statut.NON_RETENUE, utilisateur)
        self.assertEqual((candidature.decide_par, candidature.decidee), (utilisateur, True))
        self.assertEqual(candidature.date_effacement, timezone.localdate() + dt.timedelta(days=90))
        candidature.decider(Candidature.Statut.A_ETUDIER, utilisateur)
        self.assertIsNone(candidature.decide_par)

    def test_purge(self):
        gardees = [self.creer_candidature(), self.creer_candidature(statut=Candidature.Statut.NON_RETENUE,
                                                                    decide_le=timezone.now())]
        retenue_avec_bail = self.creer_candidature(statut=Candidature.Statut.RETENUE, bail=self.creer_bail())
        effacees = [
            self.creer_candidature(statut=Candidature.Statut.NON_RETENUE),
            self.creer_candidature(statut=Candidature.Statut.DESISTEMENT),
            self.creer_candidature(statut=Candidature.Statut.RETENUE),
        ]
        for candidature in effacees + [retenue_avec_bail]:
            self.vieillir(candidature, 91)
        sans_suite = self.creer_candidature()
        self.vieillir(sans_suite, 91, "modifie_le")
        sortie = io.StringIO()
        call_command("purger_candidatures", stdout=sortie)
        self.assertEqual(sortie.getvalue().strip(), "4 candidature(s) effacée(s).")
        self.assertQuerySetEqual(
            Candidature.objects.order_by("pk"), [*gardees, retenue_avec_bail], ordered=False
        )
        self.assertEqual(Candidat.objects.count(), 6)

    def test_texte_de_refus_neutre(self):
        texte = self.creer_candidature().texte_refus("Elodie")
        self.assertIn("12 avenue de Muret, 31300 Toulouse", texte)
        self.assertIn("supprimées de nos fichiers dans un délai de trois mois", texte)
        self.assertNotIn("revenu", texte.lower())


@override_settings(MFA_OBLIGATOIRE=False)
class VuesTests(Donnees, TestCase):
    def setUp(self):
        self.utilisateur = User.objects.create_user("gestion", first_name="Elodie", last_name="Durand")
        self.client.force_login(self.utilisateur)

    def donnees_formulaire(self, bien, candidats):
        donnees = {
            "bien": bien.pk, "lien_dossierfacile": LIEN, "garantie": "visale", "date_entree_souhaitee": "2026-11-01",
            "candidats-TOTAL_FORMS": str(len(candidats)), "candidats-INITIAL_FORMS": "0",
            "candidats-MIN_NUM_FORMS": "1", "candidats-MAX_NUM_FORMS": "4",
        }
        for i, nom in enumerate(candidats):
            donnees.update({f"candidats-{i}-nom": nom, f"candidats-{i}-prenom": "Julie" if nom else "",
                            f"candidats-{i}-revenus_mensuels": "2100" if nom else ""})
        return donnees

    def test_creation(self):
        bien = self.creer_bien()
        self.assertContains(self.client.get(reverse("candidatures:creer"), {"bien": bien.pk}),
                            f'<option value="{bien.pk}" selected>')
        reponse = self.client.post(reverse("candidatures:creer"), self.donnees_formulaire(bien, ["Martin", ""]))
        candidature = Candidature.objects.get()
        self.assertRedirects(reponse, candidature.get_absolute_url())
        self.assertEqual(candidature.noms, "Julie MARTIN")

    def test_refus_sans_candidat_ou_lien_etranger(self):
        bien = self.creer_bien()
        self.assertEqual(self.client.post(reverse("candidatures:creer"),
                                          self.donnees_formulaire(bien, [""])).status_code, 200)
        donnees = self.donnees_formulaire(bien, ["Martin"])
        donnees["lien_dossierfacile"] = "https://exemple.fr/dossier"
        self.assertEqual(self.client.post(reverse("candidatures:creer"), donnees).status_code, 200)
        self.assertFalse(Candidature.objects.exists())

    def test_decisions_et_reponse(self):
        candidature = self.creer_candidature()
        url = reverse("candidatures:decider", args=[candidature.pk])
        reponse = self.client.post(url, {"statut": "non_retenue"})
        self.assertRedirects(reponse, reverse("candidatures:reponse", args=[candidature.pk]))
        candidature.refresh_from_db()
        self.assertEqual(candidature.decide_par, self.utilisateur)
        page = self.client.get(reverse("candidatures:reponse", args=[candidature.pk]))
        self.assertContains(page, "Elodie Durand")
        self.client.post(reverse("candidatures:reponse", args=[candidature.pk]),
                         {"texte": "Madame, Monsieur, …", "action": "envoyer"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(sorted(mail.outbox[0].to), ["martin@exemple.fr", "petit@exemple.fr"])
        self.assertEqual(mail.outbox[0].body, "Madame, Monsieur, …")
        candidature.refresh_from_db()
        self.assertIsNotNone(candidature.refus_envoye_le)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_reponse_sans_envoi_configure(self):
        candidature = self.creer_candidature(statut=Candidature.Statut.NON_RETENUE, decide_le=timezone.now())
        url = reverse("candidatures:reponse", args=[candidature.pk])
        self.assertContains(self.client.get(url), "pas configuré")
        self.client.post(url, {"texte": "Bonjour", "action": "envoyer"})
        candidature.refresh_from_db()
        self.assertIsNone(candidature.refus_envoye_le)
        self.client.post(url, {"texte": "Bonjour", "action": "manuel"})
        candidature.refresh_from_db()
        self.assertIsNotNone(candidature.refus_envoye_le)
        self.assertEqual(mail.outbox, [])

    def test_bail_depuis_la_candidature_retenue(self):
        candidature = self.creer_candidature()
        url = reverse("baux:creer")
        self.assertEqual(self.client.get(url, {"candidature": candidature.pk}).status_code, 404)
        self.client.post(reverse("candidatures:decider", args=[candidature.pk]), {"statut": "retenue"})
        page = self.client.get(url, {"candidature": candidature.pk})
        self.assertContains(page, 'value="Petit"')
        self.assertContains(page, 'value="2026-11-01"')
        bien = candidature.bien
        donnees = {
            "candidature": candidature.pk, "bien": bien.pk, "type": "nu", "date_effet": "2026-11-01",
            "lieu_signature": "Toulouse", "loyer": "700", "mode_charges": "provision", "charges": "50",
            "jour_paiement": "1", "depot_garantie": "700", "irl_trimestre": "2e trimestre 2026",
            "irl_valeur": "146.68", "honoraires_bail_bailleur": "0", "honoraires_bail_locataire": "0",
            "honoraires_edl_bailleur": "0", "honoraires_edl_locataire": "0",
            "locataires-TOTAL_FORMS": "2", "locataires-INITIAL_FORMS": "0",
            "locataires-MIN_NUM_FORMS": "1", "locataires-MAX_NUM_FORMS": "4",
            "locataires-0-civilite": "Mme", "locataires-0-nom": "Martin", "locataires-0-prenom": "Julie",
            "locataires-1-civilite": "Mme", "locataires-1-nom": "Petit", "locataires-1-prenom": "Julie",
        }
        autre_bien = self.creer_bien(adresse="3 rue Pargaminières")
        self.assertEqual(self.client.post(url, {**donnees, "bien": autre_bien.pk}).status_code, 200)
        reponse = self.client.post(url, donnees)
        bail = Bail.objects.get()
        self.assertRedirects(reponse, bail.get_absolute_url())
        candidature.refresh_from_db()
        self.assertEqual(candidature.bail, bail)
        self.assertEqual(bail.locataires.count(), 2)
        self.assertIsNone(candidature.date_effacement)

    def test_effacement_immediat(self):
        candidature = self.creer_candidature()
        self.client.post(reverse("candidatures:effacer", args=[candidature.pk]))
        self.assertFalse(Candidature.objects.exists())
        self.assertFalse(Candidat.objects.exists())
        conservee = self.creer_candidature(statut=Candidature.Statut.RETENUE, bail=self.creer_bail())
        self.client.post(reverse("candidatures:effacer", args=[conservee.pk]))
        self.assertTrue(Candidature.objects.filter(pk=conservee.pk).exists())

    def test_pages(self):
        candidature = self.creer_candidature()
        ancienne = self.creer_candidature(statut=Candidature.Statut.NON_RETENUE)
        self.vieillir(ancienne, 120)
        for url in [reverse("candidatures:liste"), reverse("candidatures:liste") + f"?bien={candidature.bien.pk}",
                    reverse("candidatures:liste") + "?statut=a_etudier&q=martin",
                    reverse("candidatures:candidature", args=[candidature.pk]),
                    reverse("candidatures:modifier", args=[candidature.pk]),
                    reverse("biens:bien", args=[candidature.bien.pk]), reverse("accueil")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        # La liste a purgé la candidature refusée il y a quatre mois.
        self.assertFalse(Candidature.objects.filter(pk=ancienne.pk).exists())
