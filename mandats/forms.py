from django import forms

from proprietes.models import Propriete
from documents.forms import FichierSigneMixin

from .models import Mandat


class MandatForm(forms.ModelForm):
    class Meta:
        model = Mandat
        exclude = ["cree_le", "modifie_le", "fichier_signe"]
        widgets = {
            "date_signature": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_fin": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "designation": forms.Textarea(attrs={"rows": 2}),
            "observations": forms.Textarea(attrs={"rows": 3}),
        }

    SECTIONS = [
        ("Registre", ["numero"]),
        ("Bien et mandant", [
            "propriete", "mandant", "mandant_adresse", "mandant_code_postal_ville",
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
        self.fields["propriete"].queryset = Propriete.objects.filter(actif=True).select_related("bailleur")
        if self.instance.pk:
            # Le bien d'un mandat enregistré ne change plus.
            self.fields["propriete"].disabled = True
        # Le registre papier fait foi : le numéro s'y lit, il n'est pas attribué ici.
        dernier = Mandat.objects.exclude(pk=self.instance.pk).order_by("-numero").values_list("numero", flat=True).first()
        self.fields["numero"].help_text = (
            "Numéro d'inscription porté au registre papier des mandats."
            + (f" Le plus grand numéro déjà saisi ici est le {dernier}." if dernier else "")
        )
        self.fields["numero"].widget.attrs["min"] = 1

    def clean_numero(self):
        numero = self.cleaned_data["numero"]
        if numero < 1:
            raise forms.ValidationError("Le numéro au registre commence à 1.")
        return numero

    def sections(self):
        return [(titre, [self[nom] for nom in noms]) for titre, noms in self.SECTIONS]

    def clean(self):
        donnees = super().clean()
        loyer, charges = donnees.get("loyer_cc"), donnees.get("charges")
        if loyer is not None and charges is not None and charges > loyer:
            self.add_error("charges", "Les charges ne peuvent dépasser le loyer charges comprises.")
        return donnees


class FichierSigneForm(FichierSigneMixin, forms.ModelForm):
    class Meta:
        model = Mandat
        fields = ["fichier_signe"]
        widgets = {"fichier_signe": FichierSigneMixin.WIDGET}
