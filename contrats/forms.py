from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from proprietes.forms import NouveauBailleurForm, RefIcsVideEnNullMixin
from proprietes.models import Bailleur, Propriete
from core.fichiers import controler_fichier
from documents.forms import SignatureMixin

from .models import Bail, Locataire, ajouter_mois

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
# Les états des lieux et le dépôt ont leurs formulaires (EntreeForm,
# EdlSortieForm, RestitutionForm), hors de celui du bail.
CHAMPS_EDL = [
    "edl_entree_le", "edl_entree", "cles_remises", "edl_sortie", "edl_sortie_conforme",
    "depot_retenues", "depot_motif", "depot_justificatif", "depot_restitue_le",
]
FICHIER = forms.ClearableFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"})


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
        # La signature, le congé et le départ ont leur propre étape et leur
        # propre formulaire (SignatureForm, FinBailForm).
        exclude = [
            "cree_le", "modifie_le", "fichier_signe", "date_signature",
            "conge_origine", "conge_recu_le", "date_depart_prevue", "date_fin_effective",
            *CHAMPS_EDL,
        ]
        widgets = {
            **{nom: DATE for nom in ("date_effet", "precedent_date_versement", "precedent_date_revision")},
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
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["propriete"].queryset = Propriete.choix_pour(self.instance)
        propriete = self.instance.propriete if self.instance.propriete_id else None
        if propriete is not None:
            self.fields["mandat"].queryset = propriete.mandats.all()
        if self.instance.pk:
            self.fields["propriete"].disabled = True
        else:
            # Le logement est choisi à l'étape précédente.
            self.fields["propriete"].widget = forms.HiddenInput()

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
        # Bien refusé par le champ (hors de la liste) : la nouvelle fiche n'en a pas encore.
        propriete = donnees.get("propriete") or (self.instance.propriete if self.instance.propriete_id else None)
        mandat = donnees.get("mandat")
        if mandat and propriete and mandat.propriete_id != propriete.pk:
            self.add_error("mandat", "Ce mandat concerne un autre bien.")
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


def initial_fin(bail):
    """Ce que le départ du locataire propose d'avance : le congé reçu ce jour
    et la fin du préavis, puis, le préavis fini, le départ à la date prévue."""
    aujourd_hui = timezone.localdate()
    if not bail.conge_recu_le:
        return {
            "conge_origine": Bail.Origine.LOCATAIRE, "conge_recu_le": aujourd_hui,
            "date_depart_prevue": ajouter_mois(aujourd_hui, bail.mois_preavis),
        }
    if not bail.date_fin_effective and bail.date_depart_prevue <= aujourd_hui:
        return {"date_fin_effective": bail.date_depart_prevue}
    return {}


class FinBailForm(forms.ModelForm):
    """Départ du locataire, sans rouvrir tout le bail : congé reçu, fin du
    préavis, puis départ effectif."""

    class Meta:
        model = Bail
        fields = ["conge_origine", "conge_recu_le", "date_depart_prevue", "date_fin_effective"]
        widgets = {nom: DATE for nom in fields[1:]} | {"conge_origine": forms.RadioSelect}
        labels = {"date_fin_effective": "Clés rendues et EDL de sortie le", "conge_recu_le": "Congé reçu ou donné le"}
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


class CongeForm(FinBailForm):
    """La première sous-étape de la sortie : le congé et la fin du préavis,
    sans toucher à la remise des clés."""

    class Meta(FinBailForm.Meta):
        fields = ["conge_origine", "conge_recu_le", "date_depart_prevue"]
        widgets = {nom: DATE for nom in fields[1:]} | {"conge_origine": forms.RadioSelect}


class FichiersMixin:
    """Contrôle du contenu des fichiers déposés (PDF ou image)."""

    FICHIERS = ()

    def clean(self):
        donnees = super().clean()
        for nom in self.FICHIERS:
            fichier = donnees.get(nom)
            if fichier and hasattr(fichier, "content_type"):
                try:
                    controler_fichier(fichier)
                except ValidationError as erreur:
                    self.add_error(nom, erreur)
        return donnees


def _pas_dans_le_futur(form, nom):
    date = form.cleaned_data.get(nom)
    if date and date > timezone.localdate():
        form.add_error(nom, "Cette date ne peut pas être dans le futur.")


class EntreeForm(FichiersMixin, forms.ModelForm):
    """L'entrée dans les lieux : l'état des lieux d'entrée, signé."""

    FICHIERS = ("edl_entree",)

    class Meta:
        model = Bail
        fields = ["edl_entree_le", "edl_entree", "cles_remises"]
        widgets = {"edl_entree_le": DATE, "edl_entree": FICHIER}
        labels = {"edl_entree": "État des lieux signé (PDF)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["edl_entree_le"].required = True
        if not self.instance.edl_entree_le:
            self.initial["edl_entree_le"] = min(self.instance.date_effet, timezone.localdate())

    def clean(self):
        donnees = super().clean()
        _pas_dans_le_futur(self, "edl_entree_le")
        return donnees


class EdlSortieForm(FichiersMixin, forms.ModelForm):
    """La remise des clés et l'état des lieux de sortie, comparé à celui
    d'entrée : le logement redevient libre, le délai de restitution du dépôt
    court."""

    FICHIERS = ("edl_sortie",)

    class Meta:
        model = Bail
        fields = ["date_fin_effective", "edl_sortie", "edl_sortie_conforme"]
        widgets = {"date_fin_effective": DATE, "edl_sortie": FICHIER, "edl_sortie_conforme": forms.RadioSelect}
        labels = {"date_fin_effective": "Clés rendues et EDL le", "edl_sortie": "État des lieux signé (PDF)"}
        help_texts = {"date_fin_effective": ""}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date_fin_effective"].required = True
        self.fields["edl_sortie_conforme"].required = True
        self.fields["edl_sortie_conforme"].choices = [("True", "Conforme"), ("False", "Dégradations")]
        if not self.instance.date_fin_effective and self.instance.date_depart_prevue:
            self.initial["date_fin_effective"] = min(self.instance.date_depart_prevue, timezone.localdate())

    def clean(self):
        donnees = super().clean()
        _pas_dans_le_futur(self, "date_fin_effective")
        fin = donnees.get("date_fin_effective")
        if fin and fin < self.instance.date_effet:
            self.add_error("date_fin_effective", "Le départ ne peut pas précéder la prise d'effet du bail.")
        return donnees


class RestitutionForm(FichiersMixin, forms.ModelForm):
    """La restitution du dépôt de garantie, avec les retenues justifiées."""

    FICHIERS = ("depot_justificatif",)

    class Meta:
        model = Bail
        fields = ["depot_retenues", "depot_motif", "depot_justificatif", "depot_restitue_le"]
        widgets = {"depot_restitue_le": DATE, "depot_justificatif": FICHIER,
                   "depot_motif": forms.TextInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["depot_restitue_le"].required = True
        if not self.instance.depot_restitue_le:
            self.initial["depot_restitue_le"] = timezone.localdate()

    def clean(self):
        donnees = super().clean()
        _pas_dans_le_futur(self, "depot_restitue_le")
        retenues = donnees.get("depot_retenues") or 0
        if retenues < 0:
            self.add_error("depot_retenues", "Les retenues ne peuvent pas être négatives.")
        elif retenues > self.instance.depot_garantie:
            self.add_error("depot_retenues", "Les retenues ne peuvent pas dépasser le dépôt de garantie.")
        elif retenues and not donnees.get("depot_motif"):
            self.add_error("depot_motif", "Indiquer le motif des retenues.")
        restitue = donnees.get("depot_restitue_le")
        if restitue and self.instance.date_fin_effective and restitue < self.instance.date_fin_effective:
            self.add_error("depot_restitue_le", "La restitution ne peut pas précéder la remise des clés.")
        return donnees


class SignatureForm(SignatureMixin, forms.ModelForm):
    class Meta:
        model = Bail
        fields = SignatureMixin.CHAMPS
        widgets = SignatureMixin.WIDGETS
        labels = SignatureMixin.LIBELLES
