from django import forms

from core.fichiers import controler_fichier


class FichierSigneMixin:
    """Dépôt du scan d'un document signé (champ « fichier_signe »)."""

    TAILLE_MAX = 20 * 1024 * 1024
    WIDGET = forms.FileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"})

    def clean_fichier_signe(self):
        fichier = self.cleaned_data["fichier_signe"]
        if not fichier:
            raise forms.ValidationError("Choisir un fichier.")
        return controler_fichier(fichier, self.TAILLE_MAX)
