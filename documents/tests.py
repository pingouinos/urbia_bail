import io
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from core.testing import creer_gestionnaire

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document

from comptes.roles import ADMINISTRATEURS, attribuer_role
from mandats.tests import Donnees

from . import gabarits
from .generation import MODELES_PAR_DEFAUT, generer_html, texte_document
from .models import Modele


def source(nom):
    return (MODELES_PAR_DEFAUT / f"{nom}.html").read_text(encoding="utf-8")


def modele_mandat(*paragraphes):
    """Modèle de mandat d'origine augmenté de quelques paragraphes HTML."""
    return source("mandat_gestion") + "".join(f"<p>{paragraphe}</p>" for paragraphe in paragraphes)


def deposer(nom, contenu, dossier=""):
    chemin = Path(settings.MEDIA_ROOT) / "modeles" / dossier / f"{nom}.html"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


class MediaTemporaire:
    def setUp(self):
        super().setUp()
        dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dossier)
        reglage = override_settings(MEDIA_ROOT=dossier)
        reglage.enable()
        self.addCleanup(reglage.disable)
        self.admin = User.objects.create_user("elodie", first_name="Elodie")
        attribuer_role(self.admin, ADMINISTRATEURS)
        self.client.force_login(self.admin)


class GabaritsTests(TestCase):
    def test_nettoyage(self):
        sale = ('<script>alert(1)</script><h1>Titre</h1><p onclick="x()" style="color:red; text-align:center">a'
                '<a href="javascript:x">b</a><img src="x"><b>c</b><span class="x">d</span></p>'
                '<div>e</div><p style="text-align: justify">f</p><table style="min-width: 50px"><colgroup><col></colgroup>'
                '<tbody><tr><td colspan="2" style="text-align: right">g</td></tr></tbody></table>')
        self.assertEqual(gabarits.normaliser(sale), (
            '<h2>Titre</h2>\n<p style="text-align: center">ab<strong>c</strong>d</p>\n<p>e</p>\n<p>f</p>\n'
            '<table>\n<tbody>\n<tr>\n<td colspan="2" style="text-align: right"><p>g</p>\n</td>\n</tr>\n</tbody>\n</table>\n'))

    def test_expressions_refusees(self):
        for code in ["mandant.upper()", "__class__", "''.__class__", "mandant|attr('x')", "a + b"]:
            with self.subTest(code=code), self.assertRaises(gabarits.ModeleInvalide):
                gabarits.nettoyer(f'<p><span data-donnee="{code}"></span></p>')
        with self.assertRaises(gabarits.ModeleInvalide):
            gabarits.nettoyer('<div data-pour="x in y.__class__"><p>a</p></div>')

    def test_texte_libre_reste_du_texte(self):
        # Des accolades tapées dans le texte ne deviennent jamais une balise.
        corps = gabarits.remplir("<p>{{ ''.__class__ }} {% if x %} {# #}</p>", {"x": True})
        self.assertEqual(corps, "<p>&#123;&#123; ''.__class__ &#125;&#125; &#123;% if x %&#125; &#123;# #&#125;</p>")

    def test_remplissage(self):
        modele = ('<p><strong>Bailleur : </strong><span data-donnee="bailleur|upper"></span></p>'
                  '<div data-si="colocation"><p>En colocation.</p></div>'
                  '<div data-pour="locataire in locataires"><p>Locataire : <span data-donnee="locataire.nom"></span></p></div>'
                  '<p><span data-case="assurance"></span> Assurance</p>')
        corps = gabarits.remplir(modele, {"bailleur": "<b>Durand</b>", "colocation": False, "assurance": True,
                                          "locataires": [{"nom": "Martin"}, {"nom": "Ligne 1\nLigne 2"}]})
        self.assertIn("&lt;B&gt;DURAND&lt;/B&gt;", corps)
        self.assertNotIn("colocation", corps)
        self.assertIn("Locataire : Martin", corps)
        self.assertIn("Locataire : Ligne 1<br>\nLigne 2", corps)
        self.assertIn("☒ Assurance", corps)
        self.assertEqual(gabarits.variables(modele), {"bailleur", "colocation", "locataires", "assurance"})

    def test_remplacement_sur_plusieurs_mises_en_forme(self):
        modele, changements = gabarits.remplacer(
            "<p>Résilié deux <strong>mois</strong> après un commandement.</p>", {"deux mois après": "six semaines après"})
        self.assertEqual(modele, "<p>Résilié six semaines après<strong></strong> un commandement.</p>\n")
        self.assertEqual(changements[0][2:], ("Résilié deux mois après un commandement.", "Résilié six semaines après un commandement."))

    def test_conversion_d_un_modele_word(self):
        document = Document()
        document.add_paragraph("Mandat").runs[0].bold = True
        paragraphe = document.add_paragraph("Pour ")
        paragraphe.add_run("{{ mandant").italic = True
        paragraphe.add_run(" }}, à {{ agence.ville|upper }}.")
        for texte in ["{%p if retractation %}", "Droit de rétractation", "{%p endif %}",
                      "{% if assurance %}☒{% else %}☐{% endif %} Assurance", "Le mandant\t\t\tLe mandataire"]:
            document.add_paragraph(texte)
        flux = io.BytesIO()
        document.save(flux)
        self.assertEqual(gabarits.depuis_docx(flux.getvalue()), (
            '<h2>Mandat</h2>\n'
            '<p>Pour <em><span data-donnee="mandant"></span></em>, à <span data-donnee="agence.ville|upper"></span>.</p>\n'
            '<div data-si="retractation">\n<p>Droit de rétractation</p>\n</div>\n'
            '<p><span data-case="assurance"></span> Assurance</p>\n'
            '<table data-bordure="non">\n<tbody>\n<tr>\n<td><p>Le mandant</p>\n</td>\n<td><p>Le mandataire</p>\n</td>\n'
            '</tr>\n</tbody>\n</table>\n'))

    def test_modeles_livres_sont_dans_leur_forme_enregistree(self):
        for nom in ["bail_habitation", "mandat_gestion"]:
            with self.subTest(nom=nom):
                self.assertEqual(gabarits.normaliser(source(nom)), source(nom))


@override_settings(MFA_OBLIGATOIRE=False)
class ModelesTests(MediaTemporaire, Donnees, TestCase):
    def setUp(self):
        super().setUp()
        self.mandat = self.creer_mandat()
        self.url = reverse("documents:texte", args=["mandat_gestion"])

    def enregistrer(self, contenu):
        return self.client.post(self.url, {"texte": contenu}, follow=True)

    def mettre_en_service(self, **donnees):
        return self.client.post(reverse("documents:mettre_en_service", args=["mandat_gestion"]), donnees, follow=True)

    def ouvrir_essai(self):
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
            return self.client.get(reverse("documents:essai_version", args=["mandat_gestion"]))

    def texte_mandat(self):
        return texte_document(generer_html("mandat_gestion", self.mandat.contexte_document()))

    def test_modifier_puis_mettre_en_service(self):
        reponse = self.enregistrer(modele_mandat('Clause ajoutée pour <span data-donnee="mandant">Mandant</span>.'))
        self.assertContains(reponse, "Texte enregistré. Ouvrez le document d&#x27;essai")
        self.assertContains(reponse, "Version en attente d'essai : modifié dans l&#x27;application")
        # Rien ne change tant que l'essai n'est pas ouvert puis mis en service.
        self.assertNotIn("Clause ajoutée", self.texte_mandat())
        self.assertContains(self.mettre_en_service(relu="1", reference="la loi Hoguet"), "Ouvrir d&#x27;abord le document d&#x27;essai")
        self.assertNotIn("Clause ajoutée", self.texte_mandat())
        self.assertEqual(self.ouvrir_essai().content, b"%PDF-1.7")
        reponse = self.mettre_en_service(relu="1", reference="la loi Hoguet")
        self.assertContains(reponse, "mis en service, relecture notée")
        self.assertContains(reponse, "Modifié dans l&#x27;application, le")
        self.assertIn("Clause ajoutée pour Monsieur DURAND Paul et Madame MARTIN Anne.", self.texte_mandat())
        modele = Modele.objects.get(nom="mandat_gestion")
        self.assertEqual(modele.origine, Modele.Origine.EDITEUR)
        self.assertEqual((modele.relu_par.username, modele.relu_reference), ("elodie", "la loi Hoguet"))
        # Sans la case cochée, la relecture reste à faire.
        self.enregistrer(modele_mandat("Deuxième version."))
        self.ouvrir_essai()
        self.assertContains(self.mettre_en_service(reference="la loi Hoguet"), "mis en service, à relire")
        self.assertIn("Deuxième version.", self.texte_mandat())
        # Un essai abandonné ne change rien.
        self.enregistrer(modele_mandat("Troisième version."))
        self.client.post(reverse("documents:abandonner", args=["mandat_gestion"]))
        self.ouvrir_essai()
        self.mettre_en_service()
        self.assertIn("Deuxième version.", self.texte_mandat())
        # Retour à la version précédente, puis au modèle d'origine.
        self.client.post(reverse("documents:precedent", args=["mandat_gestion"]))
        self.assertIn("Clause ajoutée pour", self.texte_mandat())
        self.client.post(reverse("documents:retablir", args=["mandat_gestion"]))
        self.assertNotIn("Clause ajoutée", self.texte_mandat())

    def test_modele_refuse(self):
        for contenu, message in [
            (modele_mandat('<span data-donnee="balise_inconnue"></span>'), "Données inconnues ou placées hors de leur bloc"),
            (modele_mandat('<span data-donnee="locataire.nom"></span>'), "locataire"),
            (modele_mandat('<span data-donnee="mandant.upper(1, 2)"></span>'), "Une donnée du modèle est mal écrite"),
            ("<p></p>", "Le modèle est vide"),
        ]:
            with self.subTest(contenu=contenu[-60:]):
                self.assertContains(self.enregistrer(contenu), message)
        self.assertFalse((Path(settings.MEDIA_ROOT) / "modeles" / "essai" / "mandat_gestion.html").exists())

    def test_ancien_modele_word_converti(self):
        document = Document()
        document.add_paragraph("Mandat de l'agence pour {{ mandant }}.")
        for texte in ["{%p if retractation %}", "{%p for ligne in retractation %}", "{{ ligne }}", "{%p endfor %}",
                      "{%p endif %}", "{{ agence.mediateur }} {{ agence.rcp }}",
                      "{%p for ligne in formulaire_retractation %}", "{{ ligne }}", "{%p endfor %}"]:
            document.add_paragraph(texte)
        ancien = Path(settings.MEDIA_ROOT) / "modeles" / "mandat_gestion.docx"
        ancien.parent.mkdir(parents=True)
        document.save(ancien)
        self.assertContains(self.client.get(reverse("documents:modele", args=["mandat_gestion"])), "Mandat de gestion")
        self.assertTrue(ancien.with_suffix(".html").exists())
        self.assertTrue(ancien.with_suffix(".docx.avant-html").exists())
        self.assertIn("Mandat de l'agence pour Monsieur DURAND Paul et Madame MARTIN Anne.", self.texte_mandat())

    def test_document_essai(self):
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
            reponse = self.client.get(reverse("documents:essai", args=["mandat_gestion"]))
        self.assertEqual(reponse.content, b"%PDF-1.7")
        reponse = self.client.get(reverse("documents:essai", args=["bail_habitation"]), follow=True)
        self.assertContains(reponse, "Aucun bail d&#x27;habitation (nu ou meublé) enregistré")

    def test_essai_sans_dossier_et_sans_conversion(self):
        # Sans bail enregistré, l'essai montre le texte avec les données de
        # l'agence ; sans service de conversion, la page HTML elle-même.
        from .generation import ConversionImpossible
        url = reverse("documents:texte", args=["bail_habitation"])
        self.client.post(url, {"texte": source("bail_habitation").replace("Il a été convenu", "Il est convenu")})
        with mock.patch("documents.views.convertir_pdf", side_effect=ConversionImpossible("arrêté")):
            reponse = self.client.get(reverse("documents:essai_version", args=["bail_habitation"]))
        self.assertContains(reponse, "Il est convenu ce qui suit")
        self.assertTrue(reponse["Content-Type"].startswith("text/html"))

    def test_reserve_aux_administrateurs(self):
        self.client.force_login(creer_gestionnaire("gestion"))
        self.assertEqual(self.enregistrer(modele_mandat()).status_code, 403)
        self.assertEqual(self.client.get(reverse("documents:essai", args=["mandat_gestion"])).status_code, 403)


class ReglesTests(MediaTemporaire, TestCase):
    def test_modeles_d_origine_respectent_toutes_les_regles(self):
        from .regles import A_CORRIGER, registre
        lignes = [ligne for groupe in registre().values() for ligne in groupe]
        self.assertEqual([ligne["regle"].libelle for ligne in lignes if ligne["etat"] == A_CORRIGER], [])

    def test_modele_depose_avec_clause_perimee_est_a_corriger(self):
        from .regles import A_CORRIGER, registre
        deposer("bail_habitation", source("bail_habitation")
                + "<p>Résiliation de plein droit deux mois après un commandement de payer.</p>")
        etats = {ligne["regle"].libelle: ligne["etat"] for ligne in registre()["Bail"]}
        self.assertEqual(etats["Clause résolutoire"], A_CORRIGER)
        reponse = self.client.get(reverse("administration"))
        self.assertContains(reponse, "1 à corriger")

    def test_regle_verifiee_il_y_a_plus_d_un_an_est_a_reverifier(self):
        from datetime import timedelta
        from .regles import A_REVERIFIER, REGLES, etat
        regle = REGLES[1]
        self.assertEqual(etat(regle, {}, regle.verifiee_le + timedelta(days=366))[0], A_REVERIFIER)

    def test_page_reservee_aux_administrateurs(self):
        self.assertContains(self.client.get(reverse("documents:regles")), "Dépôt de garantie maximal")
        self.client.force_login(creer_gestionnaire("gestion"))
        self.assertEqual(self.client.get(reverse("documents:regles")).status_code, 403)

    def test_marquer_un_modele_relu(self):
        url = reverse("documents:relu", args=["mandat_gestion"])
        self.client.post(url, {"reference": "  "})
        self.assertFalse(Modele.objects.filter(relu_le__isnull=False).exists())
        self.client.post(url, {"reference": "la loi Hoguet et le décret 72-678"})
        modele = Modele.objects.get(nom="mandat_gestion")
        self.assertEqual((modele.relu_par, modele.relu_reference), (self.admin, "la loi Hoguet et le décret 72-678"))
        page = self.client.get(reverse("documents:modele", args=["mandat_gestion"]))
        self.assertContains(page, "d'après la loi Hoguet et le décret 72-678")
        self.assertContains(self.client.get(reverse("documents:modele", args=["bail_habitation"])), "Pas encore relu")


class CorrectionTests(MediaTemporaire, TestCase):
    def setUp(self):
        super().setUp()
        # Modèle de l'agence enregistré avant que la clause ne soit périmée,
        # la phrase répartie sur deux mises en forme.
        self.depose = deposer("bail_habitation", source("bail_habitation") + (
            "<p>Résilié de plein droit deux <strong>mois</strong> après un commandement de payer resté sans effet.</p>"))
        self.page = reverse("documents:corriger", args=["bail_habitation"])
        self.appliquer = reverse("documents:appliquer_correction", args=["bail_habitation"])

    def texte(self, chemin):
        return gabarits.texte(gabarits.nettoyer(chemin.read_text(encoding="utf-8")))

    def test_regle_propose_la_correction(self):
        self.assertContains(self.client.get(reverse("documents:regles")), self.page)
        page = self.client.get(self.page)
        self.assertContains(page, "<del>deux mois après un commandement de payer</del>", html=False)
        self.assertContains(page, "<ins>six semaines après un commandement de payer</ins>", html=False)
        self.assertContains(page, "disabled")

    def test_mise_en_service_exige_l_essai(self):
        self.client.post(self.appliquer)
        self.assertIn("deux mois après", self.texte(self.depose))
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
            self.client.get(reverse("documents:essai_correction", args=["bail_habitation"]))
        self.assertNotContains(self.client.get(self.page), "disabled")
        self.client.post(self.appliquer)
        texte = self.texte(self.depose)
        self.assertIn("Résilié de plein droit six semaines après un commandement de payer resté sans effet.", texte)
        self.assertNotIn("deux mois après", texte)
        modele = Modele.objects.get(nom="bail_habitation")
        self.assertEqual(modele.relu_par, self.admin)
        self.assertIn("six semaines", modele.relu_reference)
        precedent = Path(settings.MEDIA_ROOT) / "modeles" / "precedent" / "bail_habitation.html"
        self.assertIn("deux mois après", self.texte(precedent))

    def test_essai_perime_si_le_modele_change(self):
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
            self.client.get(reverse("documents:essai_correction", args=["bail_habitation"]))
        self.depose.write_text(self.depose.read_text() + "<p>Ajout de l'agence.</p>", encoding="utf-8")
        self.client.post(self.appliquer)
        self.assertIn("deux mois après", self.texte(self.depose))

    def test_sans_clause_perimee_rien_a_corriger(self):
        self.depose.unlink()
        self.assertRedirects(self.client.get(self.page), reverse("documents:modele", args=["bail_habitation"]))

    def test_liste_et_tableau_de_bord_signalent_le_modele(self):
        fiche = reverse("documents:modele", args=["bail_habitation"])
        liste = self.client.get(reverse("documents:modeles"))
        self.assertContains(liste, "Clause résolutoire : six semaines après un commandement de payer")
        self.assertContains(liste, ">Corriger</a>", html=False)
        self.assertContains(self.client.get(fiche), self.page)
        alerte = "ne respecte plus une règle légale"
        self.assertContains(self.client.get(reverse("accueil")), alerte)
        # Seul un administrateur peut corriger : l'alerte ne s'adresse qu'à lui.
        self.client.force_login(creer_gestionnaire("gestion"))
        self.assertNotContains(self.client.get(reverse("accueil")), alerte)

    def test_reserve_aux_administrateurs(self):
        self.client.force_login(creer_gestionnaire("gestion"))
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.assertEqual(self.client.get(reverse("documents:modele", args=["bail_habitation"])).status_code, 403)


@override_settings(MFA_OBLIGATOIRE=False)
class EditeurTests(MediaTemporaire, TestCase):
    """Éditeur intégré : le modèle HTML s'ouvre avec ses données en
    étiquettes et revient nettoyé."""

    def setUp(self):
        super().setUp()
        self.url = reverse("documents:texte", args=["bail_habitation"])

    def test_aller_retour_sans_changement(self):
        for nom in ["bail_habitation", "mandat_gestion"]:
            with self.subTest(nom=nom):
                editeur = gabarits.pour_editeur(gabarits.nettoyer(source(nom)))
                self.assertIn('data-donnee="bailleur">bailleur</span>' if nom == "bail_habitation"
                              else 'data-donnee="mandant">mandant</span>', editeur)
                self.assertEqual(gabarits.normaliser(editeur), source(nom))

    def test_page_de_l_editeur(self):
        page = self.client.get(self.url)
        self.assertContains(page, "data-editeur")
        self.assertContains(page, "Seulement si : bailleur email")
        self.assertContains(page, '<option value="agence.nom|upper">agence · nom</option>', html=False)
        self.assertContains(page, 'data-titre="0">I. DÉSIGNATION DES PARTIES</a>', html=False)

    def test_enregistrer_prepare_un_essai(self):
        texte = source("bail_habitation").replace("Il a été convenu ce qui suit :", "Les parties conviennent :")
        reponse = self.client.post(self.url, {"texte": texte}, follow=True)
        self.assertContains(reponse, "Texte enregistré")
        # Le modèle en service ne change qu'après l'essai et la mise en service.
        depose = Path(settings.MEDIA_ROOT) / "modeles" / "bail_habitation.html"
        self.assertFalse(depose.exists())
        self.assertContains(self.client.get(self.url), "Les parties conviennent :")
        with mock.patch("documents.views.convertir_pdf", return_value=b"%PDF-1.7"):
            self.client.get(reverse("documents:essai_version", args=["bail_habitation"]))
        self.client.post(reverse("documents:mettre_en_service", args=["bail_habitation"]),
                         {"relu": "1", "reference": "la loi de 1989"})
        self.assertIn("Les parties conviennent :", depose.read_text())
        self.assertEqual(Modele.objects.get(nom="bail_habitation").origine, Modele.Origine.EDITEUR)

    def test_texte_refuse_reste_a_l_ecran(self):
        texte = source("bail_habitation").replace("Il a été convenu", '<span data-donnee="balise_inconnue"></span>')
        reponse = self.client.post(self.url, {"texte": texte})
        self.assertContains(reponse, "Données inconnues ou placées hors de leur bloc « Pour chaque » : balise inconnue.")
        self.assertContains(reponse, "balise_inconnue")
        self.assertFalse((Path(settings.MEDIA_ROOT) / "modeles" / "essai" / "bail_habitation.html").exists())

    def test_paragraphe_a_corriger_signale(self):
        deposer("bail_habitation", source("bail_habitation")
                + "<p>Résilié deux mois après un commandement de payer resté sans effet.</p>")
        page = self.client.get(self.url)
        self.assertContains(page, "Voir la correction proposée")
        self.assertContains(page, "data-alerte")
        self.assertContains(page, "loi du 27 juillet 2023")

    def test_reserve_aux_administrateurs(self):
        self.client.force_login(creer_gestionnaire("gestion"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
