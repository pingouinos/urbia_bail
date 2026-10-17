import datetime as dt
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comptes.roles import ADMINISTRATEURS, attribuer_role
from core.testing import creer_gestionnaire

from . import zones_tendues
from .models import Bailleur, Commune, ListeCommunes, Propriete

# Même forme que le fichier publié par le ministère (décret du 22/12/2025).
ENTETE = ("CODGEO25;DEP;LIBGEO;Code EPCI;Libellé EPCI;Zonage TLV 2013;Zonage TLV 2023;"
          "Zonage TLV post décret 22/12/2025")
LIGNES = [
    "31555;31;Toulouse;Toulouse Métropole;243100518;TLV;1. Zone tendue;1. Zone tendue",
    "75056;75;Paris;Métropole du Grand Paris;200054781;TLV;1. Zone tendue;1. Zone tendue",
    "69123;69;Lyon;Métropole de Lyon;200046977;TLV;1. Zone tendue;1. Zone tendue",
    "31506;31;Saint-Orens-de-Gameville;Toulouse Métropole;243100518;TLV;1. Zone tendue;1. Zone tendue",
    "31451;31;Revel;CC Aux sources du Canal du Midi;243100567;Non TLV;3. Non tendue;3. Non tendue",
    "01045;01;Birieux;CC de la Dombes;200069193;Non TLV;2. Zone touristique et tendue;2. Zone touristique et tendue",
    # Sortie de la zone tendue par le dernier décret : la colonne la plus à droite l'emporte.
    "31999;31;Ancienne-Tendue;CC Test;200000000;TLV;1. Zone tendue;3. Non tendue",
]


def fichier(*lignes):
    return ("\r\n".join([ENTETE, *(lignes or LIGNES)]) + "\r\n").encode("utf-8")


# Bornes de plausibilité ramenées à la taille de l'échantillon.
seuils = mock.patch.multiple(zones_tendues, MIN_COMMUNES=1, ZONE_TENDUE_MIN=1, ZONE_TENDUE_MAX=10)
PUBLIEE = dt.datetime(2025, 12, 30, tzinfo=dt.timezone.utc)


@seuils
class LectureTests(TestCase):
    def test_lit_le_fichier_du_ministere(self):
        communes = {code: (nom, tendue) for code, nom, tendue in zones_tendues.lire(fichier())}
        self.assertEqual(communes["31555"], ("Toulouse", True))
        self.assertEqual(communes["31451"], ("Revel", False))
        # Les communes touristiques paient la taxe, mais les règles du bail n'y changent pas.
        self.assertFalse(communes["01045"][1])
        self.assertFalse(communes["31999"][1])

    def test_refuse_un_fichier_sans_les_communes_temoins(self):
        with self.assertRaisesMessage(zones_tendues.ListeIllisible, "Toulouse"):
            zones_tendues.lire(fichier(*[l for l in LIGNES if not l.startswith("31555")]))

    def test_refuse_un_fichier_sans_colonnes_reconnues(self):
        with self.assertRaises(zones_tendues.ListeIllisible):
            zones_tendues.lire(b"a;b;c\r\n1;2;3\r\n")

    def test_lit_un_fichier_en_windows_1252(self):
        communes = zones_tendues.lire(fichier().decode().encode("cp1252"))
        self.assertIn(("31506", "Saint-Orens-de-Gameville", True), communes)


class CleCommuneTests(TestCase):
    def test_ecritures_d_une_meme_commune(self):
        self.assertEqual(zones_tendues.cle_commune("ST ORENS DE GAMEVILLE"),
                         zones_tendues.cle_commune("Saint-Orens-de-Gameville"))
        self.assertEqual(zones_tendues.cle_commune("Paris 11e Arrondissement"), "paris")

    def test_departement(self):
        self.assertEqual(zones_tendues.departement_du_code_postal("31000"), "31")
        self.assertEqual(zones_tendues.departement_du_code_postal("97400"), "974")
        self.assertEqual(zones_tendues.departement_du_code_postal("20000"), "2A")
        self.assertEqual(zones_tendues.departement_du_code_postal("31"), "")


@seuils
class MiseAJourTests(TestCase):
    def setUp(self):
        self.bailleur = Bailleur.objects.create(nom="Martin")

    def bien(self, ville, code_postal="31000", **autres):
        return Propriete.objects.create(
            bailleur=self.bailleur, adresse="14 rue des Lois", code_postal=code_postal, ville=ville, **autres,
        )

    def charger(self, **options):
        return zones_tendues.mettre_a_jour(source=lambda: (fichier(), PUBLIEE), **options)

    def test_sans_liste_la_saisie_est_gardee(self):
        bien = self.bien("Toulouse", zone_tendue=True)
        self.assertTrue(bien.zone_tendue)
        self.assertFalse(bien.commune_reconnue)

    def test_la_liste_corrige_les_biens_et_note_les_changements(self):
        oublie = self.bien("TOULOUSE")
        coche_a_tort = self.bien("Revel", "31250", zone_tendue=True)
        faute = self.bien("Toulouze", zone_tendue=True)
        liste = self.charger()
        self.assertEqual((liste.communes, liste.en_zone_tendue), (len(LIGNES), 4))
        oublie.refresh_from_db(); coche_a_tort.refresh_from_db(); faute.refresh_from_db()
        self.assertEqual((oublie.zone_tendue, oublie.commune_reconnue), (True, True))
        self.assertEqual((coche_a_tort.zone_tendue, coche_a_tort.commune_reconnue), (False, True))
        # Commune introuvable : la saisie reste, à vérifier par l'agence.
        self.assertEqual((faute.zone_tendue, faute.commune_reconnue), (True, False))
        self.assertEqual({c["id"] for c in liste.changements}, {oublie.pk, coche_a_tort.pk})

    def test_la_commune_l_emporte_sur_la_case(self):
        self.charger()
        bien = self.bien("Saint-Orens-de-Gameville", "31650", zone_tendue=False)
        self.assertTrue(bien.zone_tendue)
        self.assertEqual(bien.commune_officielle.nom, "Saint-Orens-de-Gameville")

    def test_une_liste_recente_n_est_pas_retelechargee(self):
        self.charger()
        self.assertIsNone(self.charger())
        self.assertIsNotNone(self.charger(forcer=True))

    def test_un_echec_garde_la_liste_en_place(self):
        self.charger()

        def injoignable():
            raise OSError("data.gouv.fr injoignable")

        liste = zones_tendues.mettre_a_jour(forcer=True, source=injoignable)
        self.assertEqual(liste.erreur, "data.gouv.fr injoignable")
        self.assertEqual(Commune.objects.count(), len(LIGNES))
        self.assertEqual(zones_tendues.derniere_liste().erreur, "")

    def test_mise_a_jour_en_fond_seulement_si_la_liste_a_un_mois(self):
        self.assertTrue(zones_tendues.a_mettre_a_jour())
        self.charger()
        self.assertFalse(zones_tendues.a_mettre_a_jour())
        ListeCommunes.objects.update(le=timezone.now() - dt.timedelta(days=31))
        self.assertTrue(zones_tendues.a_mettre_a_jour())
        # Un essai en échec récent fait patienter.
        ListeCommunes.objects.create(erreur="injoignable")
        self.assertFalse(zones_tendues.a_mettre_a_jour())


@seuils
class VuesZoneTendueTests(TestCase):
    def setUp(self):
        self.client.force_login(creer_gestionnaire("gestion"))

    def demander(self, code_postal, ville):
        url = reverse("proprietes:zone_tendue")
        return self.client.get(url, {"code_postal": code_postal, "ville": ville}).json()

    def test_reponse_pendant_la_saisie(self):
        self.assertEqual(self.demander("31000", "Toulouse"), {"statut": "sans_liste"})
        zones_tendues.mettre_a_jour(source=lambda: (fichier(), PUBLIEE))
        self.assertEqual(self.demander("31000", "Toulouse"), {"statut": "oui", "commune": "Toulouse"})
        self.assertEqual(self.demander("31250", "Revel"), {"statut": "non", "commune": "Revel"})
        self.assertEqual(self.demander("31000", "Toulouze"), {"statut": "inconnue"})

    def test_fiche_et_regles_legales(self):
        admin = creer_gestionnaire("admin")
        attribuer_role(admin, ADMINISTRATEURS)
        self.client.force_login(admin)
        bien = Propriete.objects.create(
            bailleur=Bailleur.objects.create(nom="Martin"), adresse="14 rue des Lois", code_postal="31000",
            ville="Toulouse",
        )
        reponse = self.client.get(reverse("documents:regles"))
        self.assertContains(reponse, "Liste officielle pas encore téléchargée")
        zones_tendues.mettre_a_jour(source=lambda: (fichier(), PUBLIEE))
        self.assertContains(self.client.get(bien.get_absolute_url() + "?onglet=informations"),
                            "Toulouse figure sur la liste officielle")
        reponse = self.client.get(reverse("documents:regles"))
        self.assertContains(reponse, "Liste officielle du 30/12/2025, 4 communes")
        self.assertContains(reponse, "désormais en zone tendue")
