from django import forms

from biens.models import Bien

from .models import MAX_LOCATAIRES, Candidat, Candidature, Garant

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class CandidatureForm(forms.ModelForm):
    class Meta:
        model = Candidature
        fields = [
            "bien", "nombre_locataires", "lien_dossierfacile", "dossier_verifie", "garantie",
            "date_entree_souhaitee", "notes",
        ]
        widgets = {
            "nombre_locataires": forms.NumberInput(attrs={"min": 1, "max": MAX_LOCATAIRES}),
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
    # De quoi identifier le candidat et lui envoyer son lien ; il complète
    # lui-même le reste, que le collaborateur ne saisit que pour un dossier
    # remis autrement.
    ESSENTIELS = ("nom", "prenom", "email", "telephone")

    class Meta:
        model = Candidat
        fields = [
            "nom", "prenom", "email", "telephone", "civilite", "date_naissance", "lieu_naissance",
            "profession", "employeur", "contrat", "date_embauche", "revenus_mensuels",
        ]
        widgets = {"date_naissance": DATE, "date_embauche": DATE}

    @property
    def champs_essentiels(self):
        return [self[nom] for nom in self.ESSENTIELS]

    @property
    def champs_detail(self):
        return [champ for champ in self.visible_fields() if champ.name not in (*self.ESSENTIELS, "DELETE")]

    @property
    def detail_ouvert(self):
        """Déplié si une de ces informations est déjà saisie ou en erreur."""
        return any(champ.value() or champ.errors for champ in self.champs_detail)


CandidatFormSet = forms.inlineformset_factory(
    Candidature, Candidat, form=CandidatForm, extra=1, max_num=MAX_LOCATAIRES, validate_max=True,
    min_num=1, validate_min=True, can_delete=True,
)


class ReponseForm(forms.Form):
    texte = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 14}))


# Formulaire ouvert au candidat, par le lien qu'il a reçu : il ne touche
# qu'à son identité, ses coordonnées, son activité, ses revenus et son lien
# DossierFacile.
class DossierLocataireForm(forms.ModelForm):
    class Meta:
        model = Candidature
        fields = ["lien_dossierfacile"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        champ = self.fields["lien_dossierfacile"]
        champ.required = True
        champ.label = "Lien de votre dossier DossierFacile"
        champ.help_text = "Collez ici le lien de partage copié à l'étape 4."


class CandidatLocataireForm(forms.ModelForm):
    OBLIGATOIRES = ("date_naissance", "lieu_naissance", "email", "telephone", "contrat", "revenus_mensuels")

    class Meta:
        model = Candidat
        fields = [
            "civilite", "nom", "prenom", "date_naissance", "lieu_naissance", "email", "telephone",
            "profession", "employeur", "contrat", "date_embauche", "revenus_mensuels",
        ]
        widgets = {"date_naissance": DATE, "date_embauche": DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nom in self.OBLIGATOIRES:
            self.fields[nom].required = True
        self.fields["contrat"].label = "Situation professionnelle"
        self.fields["employeur"].help_text = "Ou établissement, pour un étudiant."
        self.fields["revenus_mensuels"].help_text = (
            "Salaires, pensions, bourses et allocations, avant impôt. Indiquez 0 si vous n'avez pas de revenus."
        )


def candidat_locataire_formset(candidature, data=None):
    """Une ligne à remplir par locataire annoncé sur la candidature (ou déjà
    connu), plus une ligne facultative pour un second locataire s'il n'y en a
    qu'un."""
    connus = candidature.candidats.count()
    nombre = candidature.locataires_attendus
    lignes = max(nombre, 2)
    classe = forms.inlineformset_factory(
        Candidature, Candidat, form=CandidatLocataireForm, extra=lignes - max(connus, 1),
        max_num=lignes, validate_max=True, min_num=1, validate_min=True, can_delete=False,
    )
    formset = classe(data, instance=candidature)
    formset.nombre = nombre
    for i, form in enumerate(formset.forms):
        # Une ligne annoncée ne peut pas rester vide.
        form.facultatif = i >= nombre
        if not form.facultatif:
            form.empty_permitted = False
    # Le navigateur du candidat peut remplir sa propre ligne, pas celle d'un autre.
    for nom, valeur in [("nom", "family-name"), ("prenom", "given-name"), ("date_naissance", "bday"),
                        ("email", "email"), ("telephone", "tel")]:
        formset.forms[0].fields[nom].widget.attrs["autocomplete"] = valeur
    # Les garants de chaque locataire, deux au plus.
    for i, form in enumerate(formset.forms):
        garants = GarantFormSet(data, instance=form.instance, prefix=f"garants-{i}")
        deja = garants.get_queryset().count() if form.instance.pk else 0
        garants.extra = max(GarantFormSet.max_num - deja, 0)
        form.garants = garants
        form.garants_ouverts = bool(deja) or bool(data and garants.has_changed())
    return formset


class GarantForm(forms.ModelForm):
    class Meta:
        model = Garant
        fields = ["civilite", "nom", "prenom", "adresse", "email", "telephone"]


GarantFormSet = forms.inlineformset_factory(
    Candidat, Garant, form=GarantForm, extra=0, max_num=2, validate_max=True, can_delete=True,
)
