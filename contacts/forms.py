from django import forms

from .models import Contact, cle_identite

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = [
            "categorie", "nature", "civilite", "nom", "prenom", "date_naissance", "lieu_naissance",
            "email", "telephone", "adresse", "code_postal", "ville", "notes",
        ]
        widgets = {
            "categorie": forms.RadioSelect, "nature": forms.RadioSelect, "date_naissance": DATE,
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    SECTIONS = [
        ("Identité", ["civilite", "nom", "prenom", "date_naissance", "lieu_naissance"]),
        ("Coordonnées", ["email", "telephone", "adresse", "code_postal", "ville"]),
        ("Notes", ["notes"]),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categorie"].choices = Contact.Categorie.choices
        self.fields["nature"].choices = Contact.Nature.choices
        # À la création, on indique à quel titre la personne entre au carnet.
        self.fields["categorie"].required = not self.instance.pk

    def sections(self):
        return [(titre, [self[nom] for nom in noms]) for titre, noms in self.SECTIONS]

    def clean(self):
        donnees = super().clean()
        if donnees.get("nature") == Contact.Nature.PERSONNE and not donnees.get("prenom"):
            self.add_error("prenom", "Indiquer le prénom, ou choisir « Entreprise ».")
        if donnees.get("nature") == Contact.Nature.ENTREPRISE:
            # Une société n'a ni civilité, ni prénom, ni naissance.
            donnees.update(civilite="", prenom="", date_naissance=None, lieu_naissance="")
        # Pas de doublon : même nom et même prénom, sauf dates de naissance différentes.
        if donnees.get("nom"):
            naissance = donnees.get("date_naissance")
            memes = Contact.objects.filter(cle=cle_identite(donnees["nom"], donnees.get("prenom", "")))
            for autre in memes.exclude(pk=self.instance.pk):
                if not (autre.date_naissance and naissance and autre.date_naissance != naissance):
                    self.doublon = autre
                    self.add_error("nom", "Ce contact existe déjà.")
                    break
        return donnees
