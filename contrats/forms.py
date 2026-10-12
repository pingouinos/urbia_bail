from django import forms

from proprietes.forms import NouveauBailleurForm, RefIcsVideEnNullMixin
from proprietes.models import Bailleur, Propriete
from documents.forms import SignatureMixin

from .models import Bail, Locataire

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def verifier_fin(form, donnees):
    """Le congé et le départ prévu vont ensemble, dans cet ordre."""
    conge, depart = donnees.get("conge_recu_le"), donnees.get("date_depart_prevue")
    if conge and not depart:
        form.add_error("date_depart_prevue", "Indiquer la date de fin du préavis.")
    elif depart and not conge:
        form.add_error("conge_recu_le", "Indiquer la date de réception du congé.")
    elif conge and depart and depart < conge:
        form.add_error("date_depart_prevue", "Le départ ne peut pas précéder le congé.")


class BailForm(forms.ModelForm):
    class Meta:
        model = Bail
        exclude = ["cree_le", "modifie_le", "fichier_signe", "date_signature"]
        widgets = {
            **{nom: DATE for nom in (
                "date_effet", "precedent_date_versement", "precedent_date_revision",
                "conge_recu_le", "date_depart_prevue", "date_fin_effective",
            )},
            "garants": forms.Textarea(attrs={"rows": 2}),
            "travaux": forms.Textarea(attrs={"rows": 2}),
            "conditions_particulieres": forms.Textarea(attrs={"rows": 3}),
        }

    # Sections du formulaire : (titre, champs, repliée). Les sections repliées
    # sont facultatives ou déjà préremplies ; elles s'ouvrent en cas d'erreur.
    SECTIONS = [
        ("Bail", ["propriete", "type", "usage_mixte", "garants"], False),
        ("Dates", ["date_effet", "lieu_signature"], False),
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
        ("Fin du bail", ["conge_origine", "conge_recu_le", "date_depart_prevue", "date_fin_effective"], True),
    ]
    FIN = ("conge_origine", "conge_recu_le", "date_depart_prevue", "date_fin_effective")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["propriete"].queryset = Propriete.objects.filter(actif=True).select_related("bailleur")
        propriete = self.instance.propriete if self.instance.propriete_id else None
        if propriete is not None:
            self.fields["mandat"].queryset = propriete.mandats.all()
        if self.instance.pk:
            self.fields["propriete"].disabled = True
        else:
            # Le logement est choisi à l'étape précédente, et un bail en cours
            # de rédaction n'a pas encore de fin.
            self.fields["propriete"].widget = forms.HiddenInput()
            for nom in self.FIN:
                del self.fields[nom]

    def sections(self):
        sections = []
        for titre, noms, repliee in self.SECTIONS:
            champs = [self[nom] for nom in noms if nom in self.fields and not self[nom].is_hidden]
            if not champs:
                continue
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
        propriete, mandat = donnees.get("propriete") or self.instance.propriete, donnees.get("mandat")
        if mandat and propriete and mandat.propriete_id != propriete.pk:
            self.add_error("mandat", "Ce mandat concerne un autre bien.")
        verifier_fin(self, donnees)
        # Un logement déjà loué ne se reloue qu'une fois le congé reçu, et
        # après le départ du locataire en place.
        if propriete and donnees.get("date_effet"):
            empechement = Bail.empechement(propriete, donnees["date_effet"], sauf=self.instance.pk)
            if empechement:
                self.add_error("date_effet", empechement[1])
        return donnees


class LogementForm(RefIcsVideEnNullMixin, forms.ModelForm):
    """Création rapide d'un logement pendant la rédaction d'un bail : les
    mentions que le bail exige, le reste se complète ensuite sur sa fiche."""

    proprietaire = forms.ModelChoiceField(
        Bailleur.objects.all(), required=False, label="Propriétaire déjà enregistré",
        empty_label="Nouveau propriétaire (ci-dessous)",
    )

    class Meta:
        model = Propriete
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


ProprietaireForm = NouveauBailleurForm


class LocataireForm(forms.ModelForm):
    class Meta:
        model = Locataire
        fields = [
            "civilite", "nom", "prenom", "date_naissance", "lieu_naissance", "email", "telephone", "garants",
        ]
        widgets = {"date_naissance": DATE, "garants": forms.Textarea(attrs={"rows": 2})}


# Une ligne au départ ; le bouton « Ajouter un locataire » en ajoute d'autres.
LocataireFormSet = forms.inlineformset_factory(
    Bail, Locataire, form=LocataireForm, extra=0, max_num=6, validate_max=True,
    min_num=1, validate_min=True, can_delete=True,
)


class FinBailForm(forms.ModelForm):
    """Départ du locataire, sans rouvrir tout le bail : congé reçu, fin du
    préavis, puis départ effectif."""

    class Meta:
        model = Bail
        fields = ["conge_origine", "conge_recu_le", "date_depart_prevue", "date_fin_effective"]
        widgets = {nom: DATE for nom in fields[1:]} | {"conge_origine": forms.RadioSelect}
        labels = {"date_fin_effective": "Parti le", "conge_recu_le": "Congé reçu ou donné le"}
        help_texts = {
            "conge_recu_le": "Date de réception du préavis du locataire, ou d'envoi du congé par le bailleur.",
            "date_fin_effective": "À renseigner une fois le logement rendu : il redevient libre.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["conge_origine"].choices = Bail.Origine.choices

    def clean(self):
        donnees = super().clean()
        verifier_fin(self, donnees)
        if donnees.get("conge_recu_le") and not donnees.get("conge_origine"):
            self.add_error("conge_origine", "Indiquer qui donne congé.")
        fin = donnees.get("date_fin_effective")
        if fin and fin < self.instance.date_effet:
            self.add_error("date_fin_effective", "Le départ ne peut pas précéder la prise d'effet du bail.")
        return donnees


class SignatureForm(SignatureMixin, forms.ModelForm):
    class Meta:
        model = Bail
        fields = SignatureMixin.CHAMPS
        widgets = SignatureMixin.WIDGETS
        labels = SignatureMixin.LIBELLES
