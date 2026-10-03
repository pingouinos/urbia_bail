from django import forms

from .models import Bailleur, Bien


class RefIcsVideEnNullMixin:
    """Une référence ICS laissée vide est enregistrée comme absente, pour ne
    pas heurter l'unicité entre plusieurs fiches sans référence."""

    def clean_ref_ics(self):
        return self.cleaned_data.get("ref_ics") or None


class BailleurForm(RefIcsVideEnNullMixin, forms.ModelForm):
    class Meta:
        model = Bailleur
        fields = [
            "type", "nom", "prenom", "ref_ics", "adresse", "code_postal", "ville",
            "email", "telephone", "siren",
        ]


class BienForm(RefIcsVideEnNullMixin, forms.ModelForm):
    class Meta:
        model = Bien
        exclude = ["cree_le", "modifie_le"]
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
        ("Localisation", ["adresse", "complement", "code_postal", "ville"]),
        ("Description", ["type_habitat", "regime_juridique", "periode_construction", "surface", "nb_pieces"]),
        ("Équipements", ["equipements", "chauffage", "eau_chaude", "acces_tic", "annexes", "parties_communes"]),
        ("Énergie", ["classe_dpe", "depenses_energie_min", "depenses_energie_max", "date_dpe"]),
        ("Loyer", ["zone_tendue", "loyer_reference_majore", "dernier_loyer", "charges"]),
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
