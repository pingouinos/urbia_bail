"""Avant de migrer, aligne une base créée sous les anciens noms d'application.

Les applications « baux » et « biens » sont devenues « contrats » et
« proprietes ». Django retrouve les migrations déjà appliquées par le nom de
l'application : sans cette étape, il croirait la base vide et recréerait des
tables à côté des données. Elle ne fait rien sur une base neuve ou déjà
alignée.
"""

from django.core.management.commands.migrate import Command as Migrate
from django.db import connections, transaction

RENOMMAGES = {"baux": "contrats", "biens": "proprietes"}


def aligner_applications(connexion):
    with connexion.cursor() as curseur:
        tables = connexion.introspection.table_names(curseur)
        if "django_migrations" not in tables:
            return []
        curseur.execute(
            "SELECT DISTINCT app FROM django_migrations WHERE app IN (%s)" % ", ".join(["%s"] * len(RENOMMAGES)),
            list(RENOMMAGES),
        )
        anciennes = [ligne[0] for ligne in curseur.fetchall()]
    if not anciennes:
        return []
    qn = connexion.ops.quote_name
    faits = []
    with transaction.atomic(using=connexion.alias), connexion.cursor() as curseur:
        for ancien in anciennes:
            nouveau = RENOMMAGES[ancien]
            for table in tables:
                if table.startswith(f"{ancien}_"):
                    cible = nouveau + table[len(ancien):]
                    curseur.execute(f"ALTER TABLE {qn(table)} RENAME TO {qn(cible)}")
                    faits.append(f"{table} → {cible}")
            curseur.execute("UPDATE django_migrations SET app = %s WHERE app = %s", [nouveau, ancien])
            if "django_content_type" in tables:
                curseur.execute("UPDATE django_content_type SET app_label = %s WHERE app_label = %s", [nouveau, ancien])
    return faits


class Command(Migrate):
    def handle(self, *args, **options):
        faits = aligner_applications(connections[options["database"]])
        if faits and options["verbosity"] >= 1:
            self.stdout.write("Applications renommées : " + ", ".join(faits))
        return super().handle(*args, **options)
