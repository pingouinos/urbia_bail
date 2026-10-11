from django import forms

from .models import Bailleur, Propriete


class RefIcsVideEnNullMixin:
    """Une référence ICS laissée vide est enregistrée comme absente, pour ne
    pas heurter l'unicité entre plusieurs fiches sans référence."""

    def clean_ref_ics(self):
        return self.cleaned_data.get("ref_ics") or None


class BailleurForm(RefIcsVideEnNullMixin, forms.ModelForm):
    class Meta:
        model = Bailleur
        fields = [
            "type", "civilite", "nom", "prenom", "conjoint_civilite", "conjoint_nom",
            "conjoint_prenom", "ref_ics", "adresse", "code_postal", "ville",
            "email", "telephone", "siren",
        ]


class MiseEnLocationForm(forms.ModelForm):
    """Ce qu'il faut pour proposer le logement : la date de disponibilité et
    le loyer demandé."""

    class Meta:
        model = Propriete
        fields = ["disponible_le", "dernier_loyer", "charges"]
        widgets = {"disponible_le": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        labels = {"dernier_loyer": "Loyer mensuel hors charges proposé (€)", "charges": "Charges mensuelles (€)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["disponible_le"].required = True
        self.fields["dernier_loyer"].required = True


class ProprieteForm(RefIcsVideEnNullMixin, forms.ModelForm):
    class Meta:
        model = Propriete
        exclude = ["cree_le", "modifie_le", "disponible_le"]
        widgets = {
            "date_dpe": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            **{
                nom: forms.Textarea(attrs={"rows": 3})
                for nom in ("equipements", "annexes", "parties_communes", "observations")
            },
        }

    # Regroupement des champs à l'affichage.
    SECTIONS = [
        ("Identification", ["ref_ics", "bailleur", "usage", "meuble", "actif"]),
        ("Localisation", [
            "adresse", "complement", "code_postal", "ville", "etage", "numero_appartement",
            "numero_parking", "numero_cellier", "identifiant_fiscal", "syndic",
        ]),
        ("Description", ["type_habitat", "regime_juridique", "periode_construction", "surface", "nb_pieces"]),
        ("Équipements", ["equipements", "chauffage", "eau_chaude", "acces_tic", "annexes", "parties_communes",
                          "detecteur_fumee"]),
        ("Énergie", ["classe_dpe", "depenses_energie_min", "depenses_energie_max", "date_dpe"]),
        ("Loyer", ["zone_tendue", "loyer_reference_majore", "dernier_loyer", "charges", "dispositif_fiscal"]),
        ("Notes", ["observations"]),
    ]

    def sections(self):
        return [(titre, [self[nom] for nom in noms]) for titre, noms in self.SECTIONS]

    def clean(self):
        donnees = super().clean()
        mini, maxi = donnees.get("depenses_energie_min"), donnees.get("depenses_energie_max")
        if mini is not None and maxi is not None and mini > maxi:
            self.add_error("depenses_energie_max", "Le maximum doit être supérieur au minimum.")
        return donnees


class ImportForm(forms.Form):
    fichier = forms.FileField(
        label="Fichier Excel (.xlsx) ou CSV",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.csv"}),
    )

    TAILLE_MAX = 5 * 1024 * 1024

    def clean_fichier(self):
        fichier = self.cleaned_data["fichier"]
        if not fichier.name.lower().endswith((".xlsx", ".csv")):
            raise forms.ValidationError("Fournir un fichier .xlsx ou .csv.")
        if fichier.size > self.TAILLE_MAX:
            raise forms.ValidationError("Fichier trop volumineux (5 Mo au maximum).")
        return fichier
