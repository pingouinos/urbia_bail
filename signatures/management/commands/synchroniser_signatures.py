"""Relit dans DocuSeal les demandes encore chez les signataires.

Rattrape un retour (webhook) perdu, ou le remplace quand DocuSeal ne peut pas
joindre l'application : à lancer régulièrement, par exemple toutes les
quinze minutes depuis le planificateur du serveur.
"""

from django.core.management.base import BaseCommand

from signatures import docuseal, services
from signatures.models import DemandeSignature


class Command(BaseCommand):
    help = "Met à jour les demandes de signature électronique en cours."

    def handle(self, *args, **options):
        if not docuseal.configure():
            self.stdout.write("Signature électronique non configurée : rien à faire.")
            return
        for demande in DemandeSignature.objects.filter(statut=DemandeSignature.Statut.ENVOYEE):
            try:
                services.synchroniser(demande)
            except docuseal.DocusealIndisponible as erreur:
                self.stderr.write(f"{demande} : {erreur}")
            else:
                self.stdout.write(f"{demande} : {demande.get_statut_display()}")
