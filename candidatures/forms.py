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


# Formulaire ouvert au candidat retenu, par le lien qu'il a reçu : il ne
# touche qu'à son identité, ses coordonnées et son lien DossierFacile.
class DossierLocataireForm(forms.ModelForm):
    class Meta:
        model = Candidature
        fields = ["lien_dossierfacile"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        champ = self.fields["lien_dossierfacile"]
        champ.required = True
        champ.label = "Lien de votre dossier DossierFacile"
        champ.help_text = (
            "Sur dossierfacile.logement.gouv.fr, ouvrez votre dossier puis copiez son lien de partage : "
            "il donne accès à vos pièces justificatives."
        )


class CandidatLocataireForm(forms.ModelForm):
    OBLIGATOIRES = ("date_naissance", "lieu_naissance", "email", "telephone")

    class Meta:
        model = Candidat
        fields = ["civilite", "nom", "prenom", "date_naissance", "lieu_naissance", "email", "telephone"]
        widgets = {"date_naissance": DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nom in self.OBLIGATOIRES:
            self.fields[nom].required = True


def candidat_locataire_formset(candidature, data=None):
    """Une ligne par candidat déjà connu, plus une ligne facultative pour un
    second locataire s'il n'y en a qu'un."""
    nombre = candidature.candidats.count()
    classe = forms.inlineformset_factory(
        Candidature, Candidat, form=CandidatLocataireForm, extra=1 if nombre < 2 else 0,
        max_num=max(nombre, 2), validate_max=True, min_num=1, validate_min=True, can_delete=False,
    )
    formset = classe(data, instance=candidature)
    # Le navigateur du candidat peut remplir sa propre ligne, pas celle d'un autre.
    for nom, valeur in [("nom", "family-name"), ("prenom", "given-name"), ("date_naissance", "bday"),
                        ("email", "email"), ("telephone", "tel")]:
        formset.forms[0].fields[nom].widget.attrs["autocomplete"] = valeur
    return formset
