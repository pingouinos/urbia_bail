from django import forms
from django.contrib.auth import get_user_model, password_validation

from comptes.roles import ADMINISTRATEURS, GESTIONNAIRES, attribuer_role, est_administrateur

from .models import Organisation

TAILLE_MAX_LOGO = 1024 * 1024
# Premiers octets des formats acceptés ; le SVG est écarté car il peut porter du script.
SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF": "image/webp",
}


def type_image(debut):
    """Type MIME d'une image d'après ses premiers octets, ou None."""
    for signature, type_ in SIGNATURES.items():
        if debut.startswith(signature) and (type_ != "image/webp" or debut[8:12] == b"WEBP"):
            return type_
    return None


SECTIONS = [
    ("Identité", ["nom", "raison_sociale", "forme_juridique", "capital", "siren", "rcs_ville", "logo"]),
    ("Coordonnées", ["adresse", "code_postal", "ville", "telephone", "email", "site"]),
    ("Carte professionnelle et garantie", [
        "carte_pro", "carte_delivree_par", "garant", "garant_adresse", "garantie_montant",
    ]),
    ("Valeurs proposées", ["taux_gestion"]),
]


class OrganisationForm(forms.ModelForm):
    retirer_logo = forms.BooleanField(label="Retirer le logo actuel", required=False)

    class Meta:
        model = Organisation
        fields = [champ for _, champs in SECTIONS for champ in champs]
        widgets = {"logo": forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for champ in Organisation.MENTIONS:
            self.fields[champ].required = True
        if not self.instance.logo:
            del self.fields["retirer_logo"]

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo and hasattr(logo, "content_type"):
            # Nouveau fichier déposé.
            if logo.size > TAILLE_MAX_LOGO:
                raise forms.ValidationError("Le logo dépasse 1 Mo.")
            debut = logo.read(12)
            logo.seek(0)
            if type_image(debut) is None:
                raise forms.ValidationError("Fournir une image PNG, JPEG ou WebP.")
        return logo

    def save(self, commit=True):
        if self.cleaned_data.get("retirer_logo") and not self.files.get("logo"):
            self.instance.logo = ""
        return super().save(commit)

    def sections(self):
        sections = [(titre, [self[champ] for champ in champs]) for titre, champs in SECTIONS]
        if "retirer_logo" in self.fields:
            sections[0][1].append(self["retirer_logo"])
        return sections


class UtilisateurForm(forms.ModelForm):
    """Compte d'un collaborateur : identité, rôle, accès. Le mot de passe ne
    sert qu'aux comptes sans connexion Google."""

    ROLES = [(GESTIONNAIRES, "Gestionnaire"), (ADMINISTRATEURS, "Administrateur")]

    role = forms.ChoiceField(
        label="Rôle", choices=ROLES, widget=forms.RadioSelect,
        help_text="Un administrateur gère en plus l'agence, les utilisateurs et les modèles des documents.",
    )
    mot_de_passe = forms.CharField(
        label="Nouveau mot de passe", required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="À laisser vide pour ne pas le changer. Inutile pour un compte qui se connecte avec Google.",
    )

    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "email", "is_active"]
        labels = {"is_active": "Compte actif", "email": "Adresse e-mail"}
        help_texts = {"is_active": "Décocher pour retirer l'accès sans effacer l'historique de la personne."}

    def __init__(self, *args, acteur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.acteur = acteur
        for champ in ("first_name", "last_name", "email"):
            self.fields[champ].required = True
        if self.instance.pk:
            self.initial["role"] = ADMINISTRATEURS if est_administrateur(self.instance) else GESTIONNAIRES
        else:
            self.initial["role"] = GESTIONNAIRES

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        autres = get_user_model().objects.exclude(pk=self.instance.pk)
        if autres.filter(email__iexact=email).exists() or autres.filter(username__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cette adresse.")
        return email

    def clean_mot_de_passe(self):
        mot_de_passe = self.cleaned_data.get("mot_de_passe")
        if mot_de_passe:
            password_validation.validate_password(mot_de_passe, self.instance)
        return mot_de_passe

    def clean(self):
        donnees = super().clean()
        compte = self.instance
        if compte.pk:
            perd_ses_droits = donnees.get("role") != ADMINISTRATEURS or not donnees.get("is_active")
            if perd_ses_droits and est_administrateur(compte):
                if self.acteur and compte.pk == self.acteur.pk:
                    raise forms.ValidationError("Vous ne pouvez pas retirer vos propres droits d'administrateur.")
                autres = [u for u in get_user_model().objects.filter(is_active=True).exclude(pk=compte.pk) if est_administrateur(u)]
                if not autres:
                    raise forms.ValidationError("C'est le dernier administrateur actif : nommez-en un autre d'abord.")
        return donnees

    def save(self, commit=True):
        compte = super().save(commit=False)
        if not compte.pk:
            compte.username = self.cleaned_data["email"]
            compte.set_unusable_password()
        if self.cleaned_data.get("mot_de_passe"):
            compte.set_password(self.cleaned_data["mot_de_passe"])
        compte.save()
        if not (compte.is_superuser and self.cleaned_data["role"] == ADMINISTRATEURS):
            attribuer_role(compte, self.cleaned_data["role"])
        return compte
