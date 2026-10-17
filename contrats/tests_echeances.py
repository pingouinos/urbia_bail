import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import creer_gestionnaire
from mandats.models import Mandat
from proprietes.models import Bailleur

from . import echeances
from .models import Bail, IndiceIRL, RevisionLoyer, ajouter_mois
from .tests import Donnees


def il_y_a(ans=0, jours=0):
    return ajouter_mois(timezone.localdate(), -12 * ans) - dt.timedelta(days=jours)


class RevisionTests(Donnees, TestCase):
    def bail_signe(self, date_effet, **autres):
        valeurs = {"irl_trimestre": "2e trimestre 2025", "irl_valeur": Decimal("146.68"), **autres}
        bail = self.creer_bail(date_effet=date_effet, loyer=Decimal("800"), **valeurs)
        bail.date_signature = date_effet
        bail.save()
        return bail

    def test_lecture_du_trimestre(self):
        self.assertEqual(echeances.trimestre_irl("2e trimestre 2026"), (2, 2026))
        self.assertEqual(echeances.trimestre_irl("1er trimestre 2025"), (1, 2025))
        self.assertEqual(echeances.trimestre_irl("IRL du T3 2024 : 144,51"), (3, 2024))
        self.assertIsNone(echeances.trimestre_irl("dernier connu"))

    def test_indices_charges(self):
        self.assertEqual(IndiceIRL.valeur_de(2, 2026), Decimal("148.37"))

    def test_calcul_de_la_premiere_revision(self):
        bail = self.bail_signe(dt.date(2025, 10, 1))
        revision = echeances.revision_due(bail, dt.date(2026, 9, 15))
        self.assertEqual(revision.date, dt.date(2026, 10, 1))
        self.assertEqual((revision.irl_ancien, revision.irl_nouveau), (Decimal("146.68"), Decimal("148.37")))
        # 800 × 148,37 ÷ 146,68 = 809,22
        self.assertEqual(revision.nouveau_loyer, Decimal("809.22"))
        self.assertIsNone(echeances.revision_due(bail, dt.date(2026, 8, 15)), "trop tôt")

    def test_revision_suivante_et_annee_perdue(self):
        bail = self.bail_signe(dt.date(2023, 10, 1), irl_trimestre="2e trimestre 2023", irl_valeur=Decimal("140.59"))
        RevisionLoyer.objects.create(
            bail=bail, date_revision=dt.date(2024, 10, 1), applicable_le=dt.date(2024, 10, 1),
            statut=RevisionLoyer.Statut.APPLIQUEE, ancien_loyer=Decimal("800"), nouveau_loyer=Decimal("820"),
            irl_libelle="2e trimestre 2024",
        )
        # 2025 n'a pas été traitée et elle est perdue depuis le 01/10/2026 : seule 2026 est proposée.
        revision = echeances.revision_due(bail, dt.date(2026, 10, 10))
        self.assertEqual(revision.date, dt.date(2026, 10, 1))
        self.assertEqual(revision.ancien_loyer, Decimal("820"))
        # Indices de l'année précédente et de l'année, pas de rattrapage.
        self.assertEqual((revision.irl_ancien, revision.irl_nouveau), (Decimal("146.68"), Decimal("148.37")))

    def test_logement_f_ou_g_gele(self):
        bail = self.bail_signe(dt.date(2025, 10, 1), propriete=self.creer_propriete(classe_dpe="G"))
        revision = echeances.revision_due(bail, dt.date(2026, 10, 2))
        self.assertTrue(revision.gelee)
        self.assertIsNone(revision.nouveau_loyer)

    def test_appliquer_depuis_la_fiche(self):
        self.client.force_login(creer_gestionnaire("gestion"))
        debut = il_y_a(1, -10)
        bail = self.bail_signe(debut, irl_trimestre=f"2e trimestre {debut.year - 1}", irl_valeur=Decimal("140"))
        accueil = self.client.get(reverse("accueil"))
        self.assertContains(accueil, "Réviser le loyer de Paul MARTIN")
        page = self.client.get(bail.get_absolute_url())
        self.assertContains(page, "Appliquer le nouveau loyer")
        revision = echeances.revision_due(bail)
        donnees = {"decision": "appliquer"}
        if revision.irl_nouveau is None:
            donnees["irl_nouveau"] = "150,00"
        self.client.post(reverse("contrats:reviser", args=[bail.pk]), donnees)
        bail.refresh_from_db()
        self.assertEqual(bail.revisions.get().statut, RevisionLoyer.Statut.APPLIQUEE)
        self.assertNotEqual(bail.loyer_actuel, Decimal("800"))
        self.assertEqual(bail.loyer, Decimal("800"), "le bail signé garde son loyer d'origine")
        self.assertContains(self.client.get(bail.get_absolute_url()), "Courrier au locataire")
        self.assertNotContains(self.client.get(reverse("accueil")), "Réviser le loyer")

    def test_renoncer(self):
        self.client.force_login(creer_gestionnaire("gestion"))
        bail = self.bail_signe(il_y_a(1, 5))
        self.client.post(reverse("contrats:reviser", args=[bail.pk]), {"decision": "renoncer"})
        revision = bail.revisions.get()
        self.assertEqual((revision.statut, revision.nouveau_loyer), (RevisionLoyer.Statut.RENONCEE, Decimal("800")))
        self.assertEqual(bail.loyer_actuel, Decimal("800"))


class EcheancesTests(Donnees, TestCase):
    def test_conge_du_bailleur(self):
        bail = self.creer_bail(date_effet=dt.date(2024, 1, 1))
        bail.date_signature = dt.date(2023, 12, 1)
        bail.save()
        # Fin le 31/12/2026, congé jusqu'au 30/06/2026, rappel dès le 30/04/2026.
        self.assertIsNone(echeances.conge_bailleur(bail, dt.date(2026, 4, 1)))
        self.assertEqual(echeances.conge_bailleur(bail, dt.date(2026, 5, 1)), (dt.date(2026, 12, 31), dt.date(2026, 6, 30)))
        self.assertIsNone(echeances.conge_bailleur(bail, dt.date(2026, 7, 1)))
        # Reconduit : la période suivante court jusqu'au 31/12/2029.
        self.assertEqual(echeances.fin_de_periode(bail, dt.date(2027, 2, 1)), dt.date(2029, 12, 31))

    def test_conge_meuble(self):
        bail = self.creer_bail(date_effet=dt.date(2025, 9, 1), type=Bail.Type.MEUBLE)
        bail.date_signature = dt.date(2025, 8, 20)
        bail.save()
        self.assertEqual(echeances.conge_bailleur(bail, dt.date(2026, 4, 15)), (dt.date(2026, 8, 31), dt.date(2026, 5, 31)))

    def test_assurance(self):
        bail = self.creer_bail(date_effet=dt.date(2024, 3, 10))
        bail.date_signature = dt.date(2024, 3, 1)
        bail.save()
        self.assertEqual(echeances.assurance_due(bail, dt.date(2026, 3, 1)), dt.date(2026, 3, 10))
        self.assertIsNone(echeances.assurance_due(bail, dt.date(2026, 6, 1)))
        bail.attestation_assurance_le = dt.date(2025, 9, 1)
        self.assertIsNone(echeances.assurance_due(bail, dt.date(2026, 7, 1)))
        self.assertEqual(echeances.assurance_due(bail, dt.date(2026, 10, 1)), dt.date(2026, 9, 1))

    def mandat(self, bailleur_type=Bailleur.Type.PERSONNE_PHYSIQUE, **autres):
        propriete = self.creer_propriete()
        propriete.bailleur.type = bailleur_type
        propriete.bailleur.save()
        mandat = Mandat.depuis_propriete(propriete)
        mandat.date_signature = dt.date(2024, 1, 15)
        mandat.duree_ans, mandat.reconductions = 1, 3
        for champ, valeur in autres.items():
            setattr(mandat, champ, valeur)
        mandat.save()
        return mandat

    def test_lettre_chatel(self):
        mandat = self.mandat()
        # Période jusqu'au 14/01/2027 : lettre entre le 14/10 et le 14/12/2026, rappel dès le 14/09.
        self.assertIsNone(echeances.echeance_mandat(mandat, dt.date(2026, 9, 1)))
        self.assertEqual(echeances.echeance_mandat(mandat, dt.date(2026, 10, 17)), (dt.date(2027, 1, 14), True))
        mandat.information_reconduction_le = dt.date(2026, 10, 17)
        self.assertIsNone(echeances.echeance_mandat(mandat, dt.date(2026, 10, 18)))

    def test_pas_de_lettre_pour_une_societe(self):
        mandat = self.mandat(Bailleur.Type.PERSONNE_MORALE)
        self.assertIsNone(echeances.echeance_mandat(mandat, dt.date(2026, 10, 17)))

    def test_derniere_periode(self):
        mandat = self.mandat(reconductions=0, duree_ans=3)
        self.assertEqual(echeances.echeance_mandat(mandat, dt.date(2026, 10, 1)), (dt.date(2027, 1, 14), False))

    def test_tableau_de_bord_et_lettre_notee(self):
        self.client.force_login(creer_gestionnaire("gestion"))
        aujourd_hui = timezone.localdate()
        # Période d'un an qui finit dans deux mois : la lettre est due.
        mandat = self.mandat(date_signature=ajouter_mois(aujourd_hui, -10))
        accueil = self.client.get(reverse("accueil"))
        self.assertContains(accueil, f"de l&#x27;échéance du mandat n° {mandat.numero}")
        self.assertContains(accueil, "Obligatoire")
        self.assertContains(self.client.get(mandat.get_absolute_url()), "Lettre au propriétaire")
        self.client.post(reverse("mandats:information_reconduction", args=[mandat.pk]), {})
        mandat.refresh_from_db()
        self.assertEqual(mandat.information_reconduction_le, aujourd_hui)
        self.assertNotContains(self.client.get(reverse("accueil")), "échéance du mandat")

    def test_attestation_notee(self):
        self.client.force_login(creer_gestionnaire("gestion"))
        bail = self.creer_bail(date_effet=il_y_a(2))
        bail.date_signature = bail.date_effet
        bail.save()
        self.assertContains(self.client.get(reverse("accueil")), "Redemander l&#x27;attestation d&#x27;assurance")
        self.client.post(reverse("contrats:assurance", args=[bail.pk]), {"attestation_assurance_le": "2999-01-01"})
        bail.refresh_from_db()
        self.assertEqual(bail.attestation_assurance_le, timezone.localdate(), "jamais dans le futur")
        self.assertNotContains(self.client.get(reverse("accueil")), "attestation d&#x27;assurance")
