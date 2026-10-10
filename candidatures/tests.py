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

from .models import Candidat, Candidature, Garant, valider_lien_dossierfacile

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
            "bien": bien.pk, "nombre_locataires": "1", "lien_dossierfacile": LIEN, "garantie": "visale",
            "date_entree_souhaitee": "2026-11-01",
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

    def test_creation_avec_le_nom_seul(self):
        """Le candidat complète le reste par son lien : l'état civil,
        l'activité et les revenus sont repliés, et facultatifs."""
        bien = self.creer_bien()
        page = self.client.get(reverse("candidatures:creer"), {"bien": bien.pk})
        self.assertContains(page, "<summary>État civil, activité et revenus</summary>")
        self.assertNotContains(page, "<details class=\"garants\" open>")
        # Une seule ligne à la création : le candidat qui recevra le lien.
        self.assertEqual(len(page.context["candidats"].forms), 1)
        self.assertNotContains(page, "Autre candidat, le cas échéant")
        donnees = {
            "bien": bien.pk, "nombre_locataires": "1", "garantie": "aucune",
            "candidats-TOTAL_FORMS": "1", "candidats-INITIAL_FORMS": "0",
            "candidats-MIN_NUM_FORMS": "1", "candidats-MAX_NUM_FORMS": "4",
            "candidats-0-nom": "Martin", "candidats-0-prenom": "Julie", "candidats-0-email": "julie@exemple.fr",
        }
        reponse = self.client.post(reverse("candidatures:creer"), donnees, follow=True)
        self.assertContains(reponse, "Envoyez le lien au candidat")
        candidature = Candidature.objects.get()
        self.assertEqual(candidature.email, "julie@exemple.fr")
        # Une fois l'activité connue, la section s'ouvre à la modification.
        candidature.candidats.update(revenus_mensuels=2100)
        page = self.client.get(reverse("candidatures:modifier", args=[candidature.pk]))
        self.assertContains(page, "<details class=\"garants\" open>")
        self.assertEqual(len(page.context["candidats"].forms), 2)
        self.assertContains(page, "Autre candidat, le cas échéant")

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


class LienLocataireTests(Donnees, TestCase):
    """Lien envoyé au candidat pour qu'il complète lui-même ses
    informations."""

    def setUp(self):
        self.client.force_login(User.objects.create_user("gestion", first_name="Elodie", last_name="Durand"))
        self.candidature = self.creer_candidature(
            candidats=(("Martin", 1500),), statut=Candidature.Statut.RETENUE, decide_le=timezone.now(),
            dossier_verifie=True,
        )

    def creer_lien(self):
        reponse = self.client.post(reverse("candidatures:lien", args=[self.candidature.pk]), {"action": "creer"})
        self.assertRedirects(reponse, reverse("candidatures:lien", args=[self.candidature.pk]))
        self.candidature.refresh_from_db()
        return reverse("formulaire_locataire", args=[self.candidature.jeton])

    def donnees(self, **autres):
        candidat = self.candidature.candidats.get()
        donnees = {
            "lien_dossierfacile": "https://locataire.dossierfacile.logement.gouv.fr/file/9b7c",
            "candidats-TOTAL_FORMS": "2", "candidats-INITIAL_FORMS": "1",
            "candidats-MIN_NUM_FORMS": "1", "candidats-MAX_NUM_FORMS": "2",
            "candidats-0-id": str(candidat.pk), "candidats-0-civilite": "Mme", "candidats-0-nom": "Martin-Roux",
            "candidats-0-prenom": "Julie", "candidats-0-date_naissance": "1996-03-08",
            "candidats-0-lieu_naissance": "Albi (Tarn)", "candidats-0-email": "julie@exemple.fr",
            "candidats-0-telephone": "06 12 34 56 78", "candidats-0-profession": "Infirmière",
            "candidats-0-employeur": "CHU de Toulouse", "candidats-0-contrat": "cdi",
            "candidats-0-date_embauche": "2022-09-01", "candidats-0-revenus_mensuels": "2150",
            "candidats-1-nom": "", "candidats-1-prenom": "",
        }
        for i in (0, 1):
            donnees.update({f"garants-{i}-TOTAL_FORMS": "2", f"garants-{i}-INITIAL_FORMS": "0",
                            f"garants-{i}-MIN_NUM_FORMS": "0", f"garants-{i}-MAX_NUM_FORMS": "2"})
        donnees.update(autres)
        return donnees

    def test_lien_et_message(self):
        page = self.client.get(self.candidature.get_absolute_url())
        self.assertContains(page, "Envoyer le lien au candidat")
        formulaire = self.creer_lien()
        page = self.client.get(reverse("candidatures:lien", args=[self.candidature.pk]))
        self.assertContains(page, f"http://testserver{formulaire}")
        self.assertContains(page, "ne fonctionne que depuis le réseau du bureau")
        self.assertContains(page, "mailto:martin@exemple.fr?subject=")
        self.client.post(reverse("candidatures:lien", args=[self.candidature.pk]),
                         {"action": "envoyer", "texte": page.context["form"].initial["texte"]})
        self.assertEqual(mail.outbox[0].to, ["martin@exemple.fr"])
        self.assertIn(f"http://testserver{formulaire}", mail.outbox[0].body)
        self.assertIn("valable jusqu'au", mail.outbox[0].body)
        self.assertIn("a été retenue", mail.outbox[0].body)
        self.assertIn("Votre bail", mail.outbox[0].subject)

    @override_settings(URL_LOCATAIRES="https://locataire.urbia.example")
    def test_adresse_publique(self):
        formulaire = self.creer_lien()
        page = self.client.get(reverse("candidatures:lien", args=[self.candidature.pk]))
        self.assertContains(page, f"https://locataire.urbia.example{formulaire}")
        self.assertNotContains(page, "réseau du bureau")

    def test_lien_avant_la_decision(self):
        """Le dossier se demande avant de retenir le candidat."""
        self.candidature.decider(Candidature.Statut.A_ETUDIER, None)
        self.assertContains(self.client.get(self.candidature.get_absolute_url()), "Envoyer le lien au candidat")
        formulaire = self.creer_lien()
        page = self.client.get(reverse("candidatures:lien", args=[self.candidature.pk]))
        texte = page.context["form"].initial["texte"]
        self.assertIn("Pour que nous étudiions votre dossier", texte)
        self.assertNotIn("retenue", texte)
        self.assertContains(page, "Votre%20candidature%20pour%20le%20logement")
        self.client.logout()
        page = self.client.get(formulaire)
        self.assertContains(page, "<h1>Votre candidature</h1>")
        self.assertContains(page, "Si votre candidature est retenue")
        self.assertRedirects(self.client.post(formulaire, self.donnees()), reverse("formulaire_locataire_merci"))
        self.candidature.refresh_from_db()
        self.assertEqual(self.candidature.statut, Candidature.Statut.A_ETUDIER)
        self.assertEqual(self.candidature.lien_dossierfacile, "https://locataire.dossierfacile.logement.gouv.fr/file/9b7c")
        self.assertFalse(self.candidature.dossier_verifie)

    def test_colocation_un_bloc_par_locataire(self):
        """Le nombre de locataires annoncé à l'enregistrement fixe le nombre
        de blocs à remplir, tous obligatoires."""
        Candidature.objects.filter(pk=self.candidature.pk).update(nombre_locataires=3)
        formulaire = self.creer_lien()
        page = self.client.get(reverse("candidatures:lien", args=[self.candidature.pk]))
        self.assertIn("chacun des 3 locataires", page.context["form"].initial["texte"])
        self.client.logout()
        page = self.client.get(formulaire)
        self.assertContains(page, "signé par 3 locataires")
        self.assertContains(page, "<legend>Locataire 2</legend>")
        self.assertContains(page, "<legend>Locataire 3</legend>")
        self.assertNotContains(page, "le cas échéant</legend>")
        donnees = self.donnees(**{"candidats-TOTAL_FORMS": "3", "candidats-MAX_NUM_FORMS": "3"})
        for i, prenom in ((1, "Hugo"), (2, "Lina")):
            donnees.update({f"candidats-{i}-civilite": "M.", f"candidats-{i}-nom": "Roux", f"candidats-{i}-prenom": prenom,
                            f"candidats-{i}-date_naissance": "1995-01-20", f"candidats-{i}-lieu_naissance": "Toulouse",
                            f"candidats-{i}-email": f"{prenom.lower()}@exemple.fr",
                            f"candidats-{i}-telephone": "07 00 00 00 00", f"candidats-{i}-contrat": "etudiant",
                            f"candidats-{i}-revenus_mensuels": "600"})
        donnees.update({"garants-2-TOTAL_FORMS": "2", "garants-2-INITIAL_FORMS": "0",
                        "garants-2-MIN_NUM_FORMS": "0", "garants-2-MAX_NUM_FORMS": "2"})
        # Un colocataire laissé vide : le formulaire est refusé.
        incomplet = {cle: valeur for cle, valeur in donnees.items() if not cle.startswith("candidats-2-")}
        reponse = self.client.post(formulaire, incomplet)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("nom", reponse.context["candidats"].forms[2].errors)
        self.assertEqual(self.candidature.candidats.count(), 1)
        self.assertRedirects(self.client.post(formulaire, donnees), reverse("formulaire_locataire_merci"))
        self.assertEqual([c.prenom for c in self.candidature.candidats.all()], ["Julie", "Hugo", "Lina"])
        self.assertEqual(self.candidature.revenus, Decimal("3350"))

    def test_champs_obligatoires_signales(self):
        formulaire = self.creer_lien()
        self.client.logout()
        page = self.client.get(formulaire)
        self.assertContains(page, "sont obligatoires")
        # Mini-tutoriel DossierFacile, avec les liens officiels.
        self.assertContains(page, 'href="https://www.dossierfacile.logement.gouv.fr/"')
        self.assertContains(page, 'href="https://aide.dossierfacile.logement.gouv.fr/fr/"')
        self.assertContains(page, "avec les documents justificatifs")
        self.assertContains(page, '<div class="champ obligatoire">\n  \n  <label for="id_candidats-0-nom">', html=False)

    def test_pas_de_lien_apres_un_refus(self):
        formulaire = self.creer_lien()
        for statut in (Candidature.Statut.NON_RETENUE, Candidature.Statut.DESISTEMENT):
            with self.subTest(statut=statut):
                self.candidature.decider(statut, None)
                self.assertEqual(self.client.get(formulaire).status_code, 404)
                self.assertNotContains(self.client.get(self.candidature.get_absolute_url()), "Envoyer le lien")
        self.candidature.jeton = None
        self.candidature.save()
        self.client.post(reverse("candidatures:lien", args=[self.candidature.pk]), {"action": "creer"})
        self.candidature.refresh_from_db()
        self.assertIsNone(self.candidature.jeton)

    def test_formulaire_rempli_par_le_locataire(self):
        Candidature.objects.filter(pk=self.candidature.pk).update(notes="Visite le 12, dossier fragile")
        formulaire = self.creer_lien()
        self.client.logout()
        page = self.client.get(formulaire)
        self.assertContains(page, "12 avenue de Muret")
        self.assertContains(page, 'value="Martin"')
        self.assertContains(page, "Second locataire, le cas échéant")
        self.assertContains(page, "Situation professionnelle")
        self.assertNotContains(page, "dossier fragile")  # rien d'interne
        donnees = self.donnees(**{"candidats-1-civilite": "M.", "candidats-1-nom": "Roux",
                                  "candidats-1-prenom": "Hugo", "candidats-1-date_naissance": "1995-01-20",
                                  "candidats-1-lieu_naissance": "Toulouse", "candidats-1-email": "hugo@exemple.fr",
                                  "candidats-1-telephone": "07 00 00 00 00", "candidats-1-contrat": "etudiant",
                                  "candidats-1-revenus_mensuels": "0"})
        self.assertRedirects(self.client.post(formulaire, donnees), reverse("formulaire_locataire_merci"))
        self.candidature.refresh_from_db()
        self.assertIsNone(self.candidature.jeton)
        self.assertIsNotNone(self.candidature.rempli_le)
        self.assertFalse(self.candidature.dossier_verifie)  # nouveau lien DossierFacile à vérifier
        self.assertEqual(self.candidature.lien_dossierfacile, "https://locataire.dossierfacile.logement.gouv.fr/file/9b7c")
        julie, hugo = self.candidature.candidats.all()
        self.assertEqual((julie.nom, julie.lieu_naissance, julie.telephone), ("Martin-Roux", "Albi (Tarn)", "06 12 34 56 78"))
        self.assertEqual((julie.profession, julie.contrat, julie.revenus_mensuels),
                         ("Infirmière", "cdi", Decimal("2150")))
        self.assertEqual((hugo.prenom, hugo.email, hugo.revenus_mensuels), ("Hugo", "hugo@exemple.fr", Decimal("0")))
        self.assertEqual(self.candidature.revenus, Decimal("2150"))
        # Le lien ne sert qu'une fois.
        self.assertContains(self.client.get(formulaire), "plus valable", status_code=404)
        # Le bail reprend les informations complétées.
        self.client.force_login(User.objects.get(username="gestion"))
        page = self.client.get(reverse("baux:creer"), {"candidature": self.candidature.pk})
        self.assertContains(page, 'value="Martin-Roux"')
        self.assertContains(page, 'value="Albi (Tarn)"')

    def test_champs_obligatoires(self):
        formulaire = self.creer_lien()
        self.client.logout()
        reponse = self.client.post(formulaire, self.donnees(**{"candidats-0-lieu_naissance": "",
                                                               "candidats-0-revenus_mensuels": "",
                                                               "candidats-0-civilite": "",
                                                               "lien_dossierfacile": "https://exemple.fr/x"}))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(sorted(reponse.context["candidats"].forms[0].errors),
                         ["civilite", "lieu_naissance", "revenus_mensuels"])
        self.assertContains(reponse, "Indiquer le lien de partage fourni par DossierFacile.")
        self.candidature.refresh_from_db()
        self.assertIsNotNone(self.candidature.jeton)
        self.assertEqual(self.candidature.candidats.get().lieu_naissance, "Albi")

    def test_liens_refuses(self):
        formulaire = self.creer_lien()
        self.client.logout()
        self.assertEqual(self.client.get(reverse("formulaire_locataire", args=["inconnu"])).status_code, 404)
        self.vieillir(self.candidature, 15, champ="lien_cree_le")
        self.assertEqual(self.client.get(formulaire).status_code, 404)
        # Un nouveau lien remplace l'ancien.
        self.client.force_login(User.objects.get(username="gestion"))
        nouveau = self.creer_lien()
        self.client.logout()
        self.assertEqual(self.client.get(formulaire).status_code, 404)
        self.assertEqual(self.client.get(nouveau).status_code, 200)
        # Plus de lien une fois le bail rédigé.
        self.candidature.bail = self.creer_bail(bien=self.candidature.bien)
        self.candidature.save()
        self.assertEqual(self.client.get(nouveau).status_code, 404)

    @override_settings(URL_LOCATAIRES="https://locataire.urbia.example", HOTE_LOCATAIRES="locataire.urbia.example",
                       ALLOWED_HOSTS=["locataire.urbia.example", "testserver"])
    def test_adresse_publique_limitee_au_formulaire(self):
        formulaire = self.creer_lien()
        self.assertEqual(self.client.get(formulaire, HTTP_HOST="locataire.urbia.example").status_code, 200)
        for chemin in ["/", "/connexion/", "/admin/login/", "/sante/", self.candidature.get_absolute_url()]:
            with self.subTest(chemin=chemin):
                self.assertEqual(self.client.get(chemin, HTTP_HOST="locataire.urbia.example").status_code, 404)
        # Au bureau, l'application reste entière.
        self.assertEqual(self.client.get(self.candidature.get_absolute_url()).status_code, 200)

    def test_garants(self):
        formulaire = self.creer_lien()
        self.client.logout()
        self.assertContains(self.client.get(formulaire), "Garants qui se portent caution pour vous")
        garants = {
            "garants-0-0-civilite": "Mme", "garants-0-0-nom": "Martin", "garants-0-0-prenom": "Sophie",
            "garants-0-0-adresse": "8 rue des Arts, 81000 Albi", "garants-0-0-email": "sophie@exemple.fr",
            "garants-0-1-civilite": "M.", "garants-0-1-nom": "Martin", "garants-0-1-prenom": "Alain",
            "garants-0-1-adresse": "8 rue des Arts, 81000 Albi",
        }
        # Pas de garant pour un second locataire laissé vide.
        reponse = self.client.post(formulaire, self.donnees(**garants, **{
            "garants-1-0-nom": "Roux", "garants-1-0-prenom": "Paul", "garants-1-0-adresse": "Toulouse"}))
        self.assertContains(reponse, "Indiquez d&#x27;abord ce locataire, puis ses garants.")
        # L'adresse d'un garant est obligatoire.
        reponse = self.client.post(formulaire, self.donnees(**{**garants, "garants-0-1-adresse": ""}))
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(Garant.objects.exists())
        self.assertRedirects(self.client.post(formulaire, self.donnees(**garants)),
                             reverse("formulaire_locataire_merci"))
        self.candidature.refresh_from_db()
        self.assertEqual(self.candidature.garantie, Candidature.Garantie.PERSONNE)
        self.assertEqual([g.ligne_bail for g in Garant.objects.all()], [
            "Madame MARTIN Sophie, 8 rue des Arts, 81000 Albi", "Monsieur MARTIN Alain, 8 rue des Arts, 81000 Albi",
        ])
        # La candidature les montre, le bail les reprend.
        self.client.force_login(User.objects.get(username="gestion"))
        self.assertContains(self.client.get(self.candidature.get_absolute_url()), "sophie@exemple.fr")
        page = self.client.get(reverse("baux:creer"), {"candidature": self.candidature.pk})
        self.assertContains(page, "Madame MARTIN Sophie, 8 rue des Arts, 81000 Albi\nMonsieur MARTIN Alain")
