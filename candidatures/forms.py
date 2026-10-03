from django import forms

from biens.models import Bien

from .models import Candidat, Candidature

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class CandidatureForm(forms.ModelForm):
    class Meta:
        model = Candidature
        fields = ["bien", "lien_dossierfacile", "dossier_verifie", "garantie", "date_entree_souhaitee", "notes"]
        widgets = {
            "date_entree_souhaitee": DATE,
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bien"].queryset = Bien.objects.filter(
            actif=True, usage=Bien.Usage.HABITATION
        ).select_related("bailleur")
        if self.instance.pk:
            self.fields["bien"].disabled = True


class CandidatForm(forms.ModelForm):
    class Meta:
        model = Candidat
        fields = [
            "civilite", "nom", "prenom", "date_naissance", "lieu_naissance", "email", "telephone",
            "profession", "employeur", "contrat", "date_embauche", "revenus_mensuels",
        ]
        widgets = {"date_naissance": DATE, "date_embauche": DATE}


CandidatFormSet = forms.inlineformset_factory(
    Candidature, Candidat, form=CandidatForm, extra=1, max_num=4, validate_max=True,
    min_num=1, validate_min=True, can_delete=True,
)


class ReponseForm(forms.Form):
    texte = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 14}))
