from django import forms


class FichierSigneMixin:
    """Dépôt du scan d'un document signé (champ « fichier_signe »)."""

    TAILLE_MAX = 20 * 1024 * 1024
    WIDGET = forms.FileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"})

    def clean_fichier_signe(self):
        fichier = self.cleaned_data["fichier_signe"]
        if not fichier:
            raise forms.ValidationError("Choisir un fichier.")
        if not fichier.name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
            raise forms.ValidationError("Fournir un PDF ou une image.")
        if fichier.size > self.TAILLE_MAX:
            raise forms.ValidationError("Fichier trop volumineux (20 Mo au maximum).")
        return fichier
