from django.core.management.base import BaseCommand, CommandError

from veille.catalogue import SOURCES
from veille.collecte import collecter


class Command(BaseCommand):
    help = "Contrôle les pages du catalogue de veille sans modifier les contrats."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=sorted(SOURCES), help="Limiter à une source.")

    def handle(self, *args, **options):
        erreurs = 0
        for cle in [options["source"]] if options["source"] else SOURCES:
            resultat = collecter(cle)
            self.stdout.write(f"{cle} : {resultat}")
            erreurs += resultat == "erreur"
        if erreurs:
            raise CommandError(f"{erreurs} source(s) en erreur : consulter Administration → Veille juridique.")
