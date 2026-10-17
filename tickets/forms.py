from django import forms

from core.fichiers import controler_fichier

from .models import Ticket


class TicketForm(forms.ModelForm):
    TAILLE_MAX = 5 * 1024 * 1024

    class Meta:
        model = Ticket
        fields = ["type", "texte", "capture", "page"]
        widgets = {
            "type": forms.RadioSelect,
            "texte": forms.Textarea(attrs={"rows": 3, "maxlength": 2000}),
            "capture": forms.FileInput(attrs={"accept": ".jpg,.jpeg,.png"}),
            "page": forms.HiddenInput,
        }

    def clean_texte(self):
        return self.cleaned_data["texte"].strip()[:2000]

    def clean_capture(self):
        capture = self.cleaned_data.get("capture")
        if not capture:
            return capture
        controler_fichier(capture, self.TAILLE_MAX)
        if capture.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Joindre une image (JPEG ou PNG).")
        return capture

    def clean_page(self):
        # Un chemin de l'application, jamais une adresse extérieure.
        page = self.cleaned_data.get("page", "")
        return page[:300] if page.startswith("/") and not page.startswith("//") else ""


class SuiviForm(forms.ModelForm):
    """Ce que l'exploitant met à jour sur un ticket."""

    class Meta:
        model = Ticket
        fields = ["etat", "reponse", "issue"]
        widgets = {
            "etat": forms.RadioSelect,
            "reponse": forms.Textarea(attrs={"rows": 4}),
        }
