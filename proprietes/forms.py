from django import forms

from .models import Bailleur, Immeuble, Piece, Propriete


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


class NouveauBailleurForm(forms.ModelForm):
    """Création rapide d'un bailleur pendant celle d'une propriété ou d'un
    bail : l'essentiel, le reste se complète sur sa fiche."""

    class Meta:
        model = Bailleur
        fields = ["type", "civilite", "nom", "prenom", "adresse", "code_postal", "ville", "email", "telephone"]


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
        ("Identification", ["ref_ics", "bailleur", "immeuble", "usage", "meuble", "actif"]),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            # À la création, le bailleur peut être saisi dans la foulée.
            self.fields["bailleur"].required = False
            self.fields["bailleur"].empty_label = "Nouveau bailleur (ci-dessous)"

    def sections(self):
        return [(titre, [self[nom] for nom in noms]) for titre, noms in self.SECTIONS]

    def clean(self):
        donnees = super().clean()
        mini, maxi = donnees.get("depenses_energie_min"), donnees.get("depenses_energie_max")
        if mini is not None and maxi is not None and mini > maxi:
            self.add_error("depenses_energie_max", "Le maximum doit être supérieur au minimum.")
        return donnees


class ImmeubleForm(forms.ModelForm):
    class Meta:
        model = Immeuble
        fields = ["nom", "adresse", "code_postal", "ville"]


class LotsCommunsForm(forms.ModelForm):
    """Ce que les lots d'un immeuble créés ensemble ont en commun."""

    class Meta:
        model = Propriete
        fields = [
            "bailleur", "usage", "regime_juridique", "periode_construction", "chauffage", "eau_chaude",
            "syndic", "zone_tendue", "parties_communes",
        ]
        widgets = {"parties_communes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bailleur"].required = False
        self.fields["bailleur"].empty_label = "Nouveau bailleur (ci-dessous)"


class LotForm(forms.ModelForm):
    """Une ligne du tableau des lots, à la création d'un immeuble."""

    class Meta:
        model = Propriete
        fields = ["complement", "etage", "numero_appartement", "nb_pieces", "surface", "meuble", "dernier_loyer", "charges"]
        labels = {
            "complement": "Désignation", "numero_appartement": "N° d'appt", "nb_pieces": "Pièces",
            "surface": "Surface (m²)", "dernier_loyer": "Loyer HC (€)", "charges": "Charges (€)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["complement"].help_text = ""
        self.fields["complement"].widget.attrs["placeholder"] = "Apt 3, 2e étage gauche…"

    def clean(self):
        donnees = super().clean()
        if not donnees.get("complement") and not donnees.get("numero_appartement"):
            # Sans cela, rien ne distinguerait deux lots à la même adresse.
            self.add_error("complement", "Indiquer une désignation ou un n° d'appartement.")
        return donnees


LotFormSet = forms.formset_factory(LotForm, extra=3, min_num=1, validate_min=True, max_num=60)


class PieceForm(forms.ModelForm):
    """Dépôt d'un document sur la fiche d'une propriété."""

    TAILLE_MAX = 20 * 1024 * 1024
    # Premiers octets des formats acceptés.
    SIGNATURES = (b"%PDF", b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")

    class Meta:
        model = Piece
        fields = ["type", "date_document", "libelle", "fichier"]
        widgets = {
            "date_document": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "fichier": forms.FileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}),
        }

    def clean_fichier(self):
        fichier = self.cleaned_data["fichier"]
        if not fichier.name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
            raise forms.ValidationError("Fournir un PDF ou une image (JPEG, PNG).")
        if fichier.size > self.TAILLE_MAX:
            raise forms.ValidationError("Fichier trop volumineux (20 Mo au maximum).")
        debut = fichier.read(8)
        fichier.seek(0)
        if not debut.startswith(self.SIGNATURES):
            raise forms.ValidationError("Ce fichier n'est ni un PDF ni une image.")
        return fichier


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
