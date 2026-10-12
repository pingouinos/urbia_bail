from django import forms
from django.utils import timezone

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


class SignatureMixin:
    """Enregistrement de la signature d'un contrat : sa date, et le scan de
    l'exemplaire signé s'il est disponible. La date fait passer le contrat de
    « à signer » à « signé » ; le scan seul ne suffit pas à le dire."""

    CHAMPS = ["date_signature", "fichier_signe"]
    WIDGETS = {
        "date_signature": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        "fichier_signe": FichierSigneMixin.WIDGET,
    }
    LIBELLES = {"date_signature": "Signé le", "fichier_signe": "Exemplaire signé (scan)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date_signature"].required = True
        if not self.instance.date_signature:
            self.initial["date_signature"] = timezone.localdate()

    def clean_date_signature(self):
        date = self.cleaned_data["date_signature"]
        if date > timezone.localdate():
            raise forms.ValidationError("La date de signature ne peut pas être dans le futur.")
        return date

    def clean_fichier_signe(self):
        fichier = self.cleaned_data.get("fichier_signe")
        if fichier and hasattr(fichier, "content_type"):
            controler_fichier(fichier, FichierSigneMixin.TAILLE_MAX)
        return fichier
