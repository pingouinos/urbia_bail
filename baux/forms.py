from django import forms

from biens.forms import RefIcsVideEnNullMixin
from biens.models import Bailleur, Bien
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

    # Sections du formulaire : (titre, champs, repliée). Les sections repliées
    # sont facultatives ou déjà préremplies ; elles s'ouvrent en cas d'erreur.
    SECTIONS = [
        ("Bail", ["bien", "type", "usage_mixte", "garant"], False),
        ("Dates", ["date_effet", "lieu_signature", "date_signature", "date_fin_effective"], False),
        ("Loyer et charges", [
            "loyer", "mode_charges", "charges", "jour_paiement", "a_echoir", "depot_garantie",
            "irl_trimestre", "irl_valeur",
        ], False),
        ("Mandat et honoraires de location", [
            "mandat", "honoraires_bail_bailleur", "honoraires_bail_locataire",
            "honoraires_edl_bailleur", "honoraires_edl_locataire",
        ], True),
        ("Précédent locataire (zone tendue)", [
            "precedent_loyer", "precedent_date_versement", "precedent_date_revision", "travaux",
        ], True),
        ("Conditions particulières", ["conditions_particulieres"], True),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bien"].queryset = Bien.objects.filter(actif=True).select_related("bailleur")
        bien = self.instance.bien if self.instance.bien_id else None
        if bien is not None:
            self.fields["mandat"].queryset = bien.mandats.all()
        if self.instance.pk:
            self.fields["bien"].disabled = True
        else:
            # Le logement est choisi à l'étape précédente, et un bail en cours
            # de rédaction n'a pas encore de fin.
            self.fields["bien"].widget = forms.HiddenInput()
            del self.fields["date_fin_effective"]

    def sections(self):
        sections = []
        for titre, noms, repliee in self.SECTIONS:
            champs = [self[nom] for nom in noms if nom in self.fields and not self[nom].is_hidden]
            sections.append({
                "titre": titre, "champs": champs, "repliee": repliee,
                "en_erreur": any(champ.errors for champ in champs),
            })
        return sections

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


class LogementForm(RefIcsVideEnNullMixin, forms.ModelForm):
    """Création rapide d'un logement pendant la rédaction d'un bail : les
    mentions que le bail exige, le reste se complète ensuite sur sa fiche."""

    proprietaire = forms.ModelChoiceField(
        Bailleur.objects.all(), required=False, label="Propriétaire déjà enregistré",
        empty_label="Nouveau propriétaire (ci-dessous)",
    )

    class Meta:
        model = Bien
        fields = [
            "adresse", "complement", "code_postal", "ville", "ref_ics", "meuble", "type_habitat",
            "regime_juridique", "periode_construction", "surface", "nb_pieces", "chauffage", "eau_chaude",
            "classe_dpe", "equipements", "zone_tendue", "dernier_loyer", "charges",
        ]
        labels = {"dernier_loyer": "Loyer mensuel hors charges (€)", "charges": "Charges mensuelles (€)"}
        widgets = {"equipements": forms.Textarea(attrs={"rows": 3})}

    SECTIONS = [
        ("Adresse", ["adresse", "complement", "code_postal", "ville", "ref_ics"]),
        ("Description", [
            "meuble", "type_habitat", "regime_juridique", "periode_construction", "surface", "nb_pieces",
            "chauffage", "eau_chaude", "classe_dpe", "equipements",
        ]),
        ("Loyer", ["dernier_loyer", "charges", "zone_tendue"]),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Mentions obligatoires du bail type.
        for nom in ("type_habitat", "regime_juridique", "periode_construction", "surface", "nb_pieces",
                    "chauffage", "eau_chaude", "classe_dpe", "dernier_loyer"):
            self.fields[nom].required = True

    def sections(self):
        return [(titre, [self[nom] for nom in noms]) for titre, noms in self.SECTIONS]


class ProprietaireForm(forms.ModelForm):
    class Meta:
        model = Bailleur
        fields = ["type", "civilite", "nom", "prenom", "adresse", "code_postal", "ville", "email", "telephone"]


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
