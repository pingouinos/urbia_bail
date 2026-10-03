from django import forms

from biens.models import Bien
from documents.forms import FichierSigneMixin

from .models import Bail, Locataire

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class BailForm(forms.ModelForm):
    class Meta:
        model = Bail
        exclude = ["cree_le", "modifie_le", "fichier_signe"]
        widgets = {
            **{nom: DATE for nom in (
                "date_effet", "precedent_date_versement", "precedent_date_revision",
                "date_signature", "date_fin_effective",
            )},
            "travaux": forms.Textarea(attrs={"rows": 2}),
            "conditions_particulieres": forms.Textarea(attrs={"rows": 3}),
        }

    SECTIONS = [
        ("Bien et bail", ["bien", "mandat", "type", "usage_mixte", "garant"]),
        ("Durée", ["date_effet", "date_signature", "lieu_signature", "date_fin_effective"]),
        ("Loyer et charges", [
            "loyer", "mode_charges", "charges", "jour_paiement", "a_echoir", "depot_garantie",
            "irl_trimestre", "irl_valeur",
        ]),
        ("Précédent locataire (zone tendue)", [
            "precedent_loyer", "precedent_date_versement", "precedent_date_revision", "travaux",
        ]),
        ("Honoraires de location", [
            "honoraires_bail_bailleur", "honoraires_bail_locataire",
            "honoraires_edl_bailleur", "honoraires_edl_locataire",
        ]),
        ("Conditions particulières", ["conditions_particulieres"]),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bien"].queryset = Bien.objects.filter(actif=True).select_related("bailleur")
        bien = self.instance.bien if self.instance.bien_id else None
        if bien is not None:
            self.fields["mandat"].queryset = bien.mandats.all()
        if self.instance.pk:
            self.fields["bien"].disabled = True

    def sections(self):
        return [(titre, [self[nom] for nom in noms]) for titre, noms in self.SECTIONS]

    def clean_jour_paiement(self):
        jour = self.cleaned_data["jour_paiement"]
        if not 1 <= jour <= 28:
            raise forms.ValidationError("Choisir un jour entre 1 et 28.")
        return jour

    def clean(self):
        donnees = super().clean()
        bien, mandat = donnees.get("bien") or self.instance.bien, donnees.get("mandat")
        if mandat and bien and mandat.bien_id != bien.pk:
            self.add_error("mandat", "Ce mandat concerne un autre bien.")
        return donnees


class LocataireForm(forms.ModelForm):
    class Meta:
        model = Locataire
        fields = ["civilite", "nom", "prenom", "date_naissance", "lieu_naissance", "email", "telephone"]
        widgets = {"date_naissance": DATE}


LocataireFormSet = forms.inlineformset_factory(
    Bail, Locataire, form=LocataireForm, extra=1, max_num=4, validate_max=True,
    min_num=1, validate_min=True, can_delete=True,
)


class FichierSigneForm(FichierSigneMixin, forms.ModelForm):
    class Meta:
        model = Bail
        fields = ["fichier_signe"]
        widgets = {"fichier_signe": FichierSigneMixin.WIDGET}
