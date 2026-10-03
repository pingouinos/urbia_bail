from django import forms

from biens.models import Bien

from .models import Mandat


class MandatForm(forms.ModelForm):
    class Meta:
        model = Mandat
        exclude = ["numero", "cree_le", "modifie_le", "fichier_signe"]
        widgets = {
            "date_signature": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_fin": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "designation": forms.Textarea(attrs={"rows": 2}),
            "observations": forms.Textarea(attrs={"rows": 3}),
        }

    SECTIONS = [
        ("Bien et mandant", [
            "bien", "mandant", "mandant_adresse", "mandant_code_postal_ville",
            "designation", "adresse_bien", "loyer_cc", "charges",
        ]),
        ("Durée", ["date_signature", "duree_ans", "reconductions", "date_fin"]),
        ("Gestion", ["taux_gestion", "base_gestion", "assurance_loyers_impayes", "taux_assurance"]),
        ("Honoraires de location", [
            "honoraires_bail_bailleur", "honoraires_bail_locataire",
            "honoraires_edl_bailleur", "honoraires_edl_locataire",
        ]),
        ("Notes", ["observations"]),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bien"].queryset = Bien.objects.filter(actif=True).select_related("bailleur")
        if self.instance.pk:
            # Le bien d'un mandat enregistré ne change plus.
            self.fields["bien"].disabled = True

    def sections(self):
        return [(titre, [self[nom] for nom in noms]) for titre, noms in self.SECTIONS]

    def clean(self):
        donnees = super().clean()
        loyer, charges = donnees.get("loyer_cc"), donnees.get("charges")
        if loyer is not None and charges is not None and charges > loyer:
            self.add_error("charges", "Les charges ne peuvent dépasser le loyer charges comprises.")
        return donnees


class FichierSigneForm(forms.ModelForm):
    class Meta:
        model = Mandat
        fields = ["fichier_signe"]
        widgets = {"fichier_signe": forms.FileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"})}

    TAILLE_MAX = 20 * 1024 * 1024

    def clean_fichier_signe(self):
        fichier = self.cleaned_data["fichier_signe"]
        if not fichier:
            raise forms.ValidationError("Choisir un fichier.")
        if not fichier.name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
            raise forms.ValidationError("Fournir un PDF ou une image.")
        if fichier.size > self.TAILLE_MAX:
            raise forms.ValidationError("Fichier trop volumineux (20 Mo au maximum).")
        return fichier
