from django import forms
from django.utils import timezone

from proprietes.models import Propriete
from core.fichiers import controler_fichier
from documents.forms import FichierSigneMixin, SignatureMixin

from .models import (
    PLAFOND_BAIL_HORS_ZONE_TENDUE, PLAFOND_BAIL_ZONE_TENDUE, PLAFOND_ETAT_DES_LIEUX, Mandat, plafonds_locataire,
)


class MandatForm(forms.ModelForm):
    class Meta:
        model = Mandat
        exclude = ["cree_le", "modifie_le", "fichier_signe", "date_signature"]
        widgets = {
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
        ("Durée", ["duree_ans", "reconductions", "date_fin"]),
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

    @property
    def base_honoraires(self):
        """Rappel des calculs proposés : « 39 m² × 10 €/m² (zone tendue) = 390 € »."""
        from documents.generation import montant

        propriete = getattr(self.instance, "propriete", None)
        plafonds = plafonds_locataire(propriete) if propriete else None
        if not plafonds:
            return []
        tendue = propriete.zone_tendue
        taux = PLAFOND_BAIL_ZONE_TENDUE if tendue else PLAFOND_BAIL_HORS_ZONE_TENDUE
        surface = montant(propriete.surface)
        return [
            f"Visite, dossier et bail : {surface} m² × {montant(taux)} €/m² "
            f"({'zone tendue' if tendue else 'hors zone tendue'}) = {montant(plafonds[0])} €",
            f"État des lieux : {surface} m² × {montant(PLAFOND_ETAT_DES_LIEUX)} €/m² = {montant(plafonds[1])} €",
        ]


class SignatureForm(SignatureMixin, forms.ModelForm):
    class Meta:
        model = Mandat
        fields = SignatureMixin.CHAMPS
        widgets = SignatureMixin.WIDGETS
        labels = SignatureMixin.LIBELLES
