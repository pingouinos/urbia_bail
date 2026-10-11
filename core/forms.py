from django import forms

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
