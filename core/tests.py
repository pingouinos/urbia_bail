from django.db import connection
from django.test import TestCase

from .management.commands.migrate import aligner_applications


class AlignementApplicationsTests(TestCase):
    """Une base créée sous les anciens noms d'application (baux, biens) est
    alignée avant les migrations ; une base neuve n'est pas touchée."""

    def test_base_deja_alignee(self):
        self.assertEqual(aligner_applications(connection), [])

    def test_anciens_noms(self):
        with connection.cursor() as curseur:
            curseur.execute("CREATE TABLE baux_essai (id integer)")
            curseur.execute("CREATE TABLE biens_essai (id integer)")
            for application in ("baux", "biens"):
                curseur.execute(
                    "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, CURRENT_TIMESTAMP)",
                    [application, "0001_initial"],
                )
            curseur.execute("INSERT INTO django_content_type (app_label, model) VALUES (%s, %s)", ["baux", "essai"])
        faits = aligner_applications(connection)
        self.assertEqual(sorted(faits), ["baux_essai → contrats_essai", "biens_essai → proprietes_essai"])
        with connection.cursor() as curseur:
            tables = connection.introspection.table_names(curseur)
            curseur.execute("SELECT app FROM django_migrations WHERE name = %s AND app IN (%s, %s, %s, %s)",
                            ["0001_initial", "baux", "biens", "contrats", "proprietes"])
            applications = {ligne[0] for ligne in curseur.fetchall()}
            curseur.execute("SELECT app_label FROM django_content_type WHERE model = %s", ["essai"])
            self.assertEqual(curseur.fetchone()[0], "contrats")
        self.assertIn("contrats_essai", tables)
        self.assertIn("proprietes_essai", tables)
        self.assertNotIn("baux_essai", tables)
        self.assertEqual(applications, {"contrats", "proprietes"})
        # Rejouer l'alignement ne fait plus rien.
        self.assertEqual(aligner_applications(connection), [])
