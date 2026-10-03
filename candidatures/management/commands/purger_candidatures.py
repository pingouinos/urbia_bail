from django.core.management.base import BaseCommand

from candidatures.models import Candidature


class Command(BaseCommand):
    help = "Efface les candidatures non retenues depuis plus de trois mois (référentiel CNIL)."

    def handle(self, *args, **options):
        nombre = Candidature.purger()
        self.stdout.write(f"{nombre} candidature(s) effacée(s).")
