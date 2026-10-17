import re

from django import forms
from django.utils import timezone

from proprietes.models import Bailleur, Piece, Propriete
from core.champs import normaliser_telephone
from core.fichiers import controler_fichier
from documents.forms import FichierSigneMixin, SignatureMixin

from .models import (
    PLAFOND_BAIL_HORS_ZONE_TENDUE, PLAFOND_BAIL_ZONE_TENDUE, PLAFOND_ETAT_DES_LIEUX, Mandat, plafonds_locataire,
)


class MandatCourtForm(forms.Form):
    """Mandat court : l'agence ne saisit que de quoi joindre le propriétaire
    et retrouver le bien ; il complète sa fiche par le lien qu'il reçoit."""

    nom = forms.CharField(label="Nom du propriétaire", max_length=200)
    prenom = forms.CharField(label="Prénom", max_length=100)
    email = forms.EmailField(label="E-mail")
    telephone = Bailleur._meta.get_field("telephone").formfield(label="Téléphone portable", required=False)
    adresse = forms.CharField(label="Adresse du bien confié", max_length=255)
    code_postal = Propriete._meta.get_field("code_postal").formfield()
    ville = forms.CharField(label="Ville", max_length=100)

    def clean_telephone(self):
        return normaliser_telephone(self.cleaned_data.get("telephone"))

    def creer(self):
        """Bailleur, propriété et mandat en attente du propriétaire."""
        donnees = self.cleaned_data
        bailleur = Bailleur.objects.create(
            nom=donnees["nom"], prenom=donnees["prenom"], email=donnees["email"], telephone=donnees["telephone"],
        )
        propriete = Propriete.objects.create(
            bailleur=bailleur, adresse=donnees["adresse"], code_postal=donnees["code_postal"], ville=donnees["ville"],
        )
        mandat = Mandat.depuis_propriete(propriete)
        # La désignation se rédige une fois le bien décrit : un T0 de 0 m² n'a rien à faire au mandat.
        mandat.designation = ""
        mandat.save()
        mandat.creer_lien()
        return mandat


class MandatForm(forms.ModelForm):
    class Meta:
        model = Mandat
        exclude = ["cree_le", "modifie_le", "fichier_signe", "date_signature", "date_fin"]
        widgets = {
            "designation": forms.Textarea(attrs={"rows": 2}),
            "observations": forms.Textarea(attrs={"rows": 3}),
        }

    SECTIONS = [
        ("Registre", ["numero"]),
        ("Bien et mandant", [
            "propriete", "mandant", "mandant_adresse", "mandant_code_postal_ville",
            "designation", "adresse_bien", "loyer_cc", "charges",
        ]),
        ("Signature", ["conclusion", "lieu_signature"]),
        ("Durée", ["duree_ans", "reconductions"]),
        ("Gestion", ["taux_gestion", "base_gestion", "assurance_loyers_impayes", "taux_assurance"]),
        ("Honoraires de location", [
            "honoraires_bail_bailleur", "honoraires_bail_locataire",
            "honoraires_edl_bailleur", "honoraires_edl_locataire",
        ]),
        ("Notes", ["observations"]),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["propriete"].queryset = Propriete.choix_pour(self.instance)
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
        self.fields["conclusion"].required = True

    def clean_numero(self):
        numero = self.cleaned_data["numero"]
        if numero < 1:
            raise forms.ValidationError("Le numéro au registre commence à 1.")
        # Registre de l'agence connectée : le manager ne lit que ses mandats.
        if Mandat.objects.exclude(pk=self.instance.pk).filter(numero=numero).exists():
            raise forms.ValidationError("Ce numéro est déjà porté par un autre mandat.")
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


class FinMandatForm(forms.ModelForm):
    """Fin du mandat (résiliation ou échéance), sans rouvrir tout le mandat."""

    class Meta:
        model = Mandat
        fields = ["date_fin"]
        widgets = {"date_fin": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        labels = {"date_fin": "Fin du mandat le"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date_fin"].required = True
        if not self.instance.date_fin:
            self.initial["date_fin"] = timezone.localdate()

    def clean_date_fin(self):
        date = self.cleaned_data["date_fin"]
        if self.instance.date_signature and date < self.instance.date_signature:
            raise forms.ValidationError("La fin du mandat ne peut pas précéder sa signature.")
        return date


class SignatureForm(SignatureMixin, forms.ModelForm):
    class Meta:
        model = Mandat
        fields = SignatureMixin.CHAMPS
        widgets = SignatureMixin.WIDGETS
        labels = SignatureMixin.LIBELLES


# Formulaires ouverts au propriétaire, par le lien qu'il a reçu : il ne
# touche qu'à ses coordonnées, au versement de ses loyers et à ses pièces.
class ProprietaireVousForm(forms.ModelForm):
    PERSONNE = [
        "civilite", "nom", "prenom", "conjoint_civilite", "conjoint_nom", "conjoint_prenom",
        "adresse", "code_postal", "ville", "telephone", "email",
    ]
    SOCIETE = [
        "representant_civilite", "representant_nom", "representant_prenom", "representant_qualite",
        "adresse", "code_postal", "ville", "telephone", "email",
    ]

    class Meta:
        model = Bailleur
        fields = sorted(set(["civilite", "nom", "prenom", "conjoint_civilite", "conjoint_nom", "conjoint_prenom",
                             "representant_civilite", "representant_nom", "representant_prenom",
                             "representant_qualite", "adresse", "code_postal", "ville", "telephone", "email"]))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # La raison sociale d'une société ne se corrige qu'à l'agence.
        gardes = self.SOCIETE if self.instance.societe else self.PERSONNE
        for nom in list(self.fields):
            if nom not in gardes:
                del self.fields[nom]
        self.order_fields(gardes)
        libelles = {
            "nom": "Nom", "conjoint_nom": "Nom du conjoint ou co-propriétaire, le cas échéant",
            "representant_nom": "Nom de la personne qui signe pour la société",
            "representant_prenom": "Son prénom", "representant_civilite": "Sa civilité",
            "representant_qualite": "Sa qualité (gérant, gérante…)",
        }
        for nom, libelle in libelles.items():
            if nom in self.fields:
                self.fields[nom].label = libelle
        for nom in ("adresse", "code_postal", "ville", "email"):
            self.fields[nom].required = True


class VersementForm(forms.ModelForm):
    class Meta:
        model = Bailleur
        fields = ["versement", "iban"]
        widgets = {"versement": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["versement"].choices = Bailleur.Versement.choices
        self.fields["versement"].label = "Rythme"
        self.fields["iban"].label = "IBAN du compte qui reçoit les loyers"
        self.fields["iban"].widget.attrs.update({"placeholder": "FR76 …", "autocomplete": "off", "inputmode": "text"})

    def clean_iban(self):
        iban = re.sub(r"\s", "", self.cleaned_data["iban"]).upper()
        return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


class PieceProprietaireForm(forms.Form):
    """Une pièce du dossier du mandant, déposée par le propriétaire."""

    TAILLE_MAX = 10 * 1024 * 1024

    type = forms.ChoiceField(choices=Piece.Type.choices, widget=forms.HiddenInput)
    fichier = forms.FileField(widget=forms.FileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png,image/*"}))

    def __init__(self, *args, types=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["type"].choices = [(t, t) for t in types]

    def clean_fichier(self):
        return controler_fichier(self.cleaned_data["fichier"], self.TAILLE_MAX)
