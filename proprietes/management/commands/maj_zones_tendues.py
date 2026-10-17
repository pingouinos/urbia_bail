from django.core.management.base import BaseCommand

from proprietes.zones_tendues import mettre_a_jour


class Command(BaseCommand):
    help = (
        "Télécharge la liste officielle des communes en zone tendue (data.gouv.fr) si elle a plus d'un mois, "
        "puis recalcule la zone tendue des biens."
    )

    def add_arguments(self, parser):
        parser.add_argument("--forcer", action="store_true", help="Télécharger même si la liste est récente.")

    def handle(self, *args, forcer=False, **options):
        liste = mettre_a_jour(forcer=forcer)
        if liste is None:
            self.stdout.write("Liste des zones tendues déjà à jour.")
        elif liste.erreur:
            self.stderr.write(f"Liste des zones tendues non mise à jour : {liste.erreur}")
        else:
            self.stdout.write(
                f"{liste.en_zone_tendue} communes en zone tendue sur {liste.communes} ; "
                f"{len(liste.changements)} bien(s) changé(s)."
            )
