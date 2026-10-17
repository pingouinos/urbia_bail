from django import forms

from core.fichiers import controler_fichier

from .models import Associe, Bailleur, Immeuble, Piece, Propriete, cle_adresse


class SocieteOuPersonneMixin:
    """Une société porte un nom et un représentant ; une personne, une
    civilité, un prénom et éventuellement un conjoint. Les champs qui ne
    concernent pas le type choisi sont vidés."""

    doublon = None
    PERSONNE = ("civilite", "prenom", "conjoint_civilite", "conjoint_nom", "conjoint_prenom")
    SOCIETE = ("representant_civilite", "representant_nom", "representant_prenom", "representant_qualite", "siren")

    def clean(self):
        donnees = super().clean()
        if donnees.get("type"):
            societe = donnees["type"] != Bailleur.Type.PERSONNE_PHYSIQUE
            for champ in self.PERSONNE if societe else self.SOCIETE:
                if champ in self.fields and champ not in self.errors:
                    donnees[champ] = ""
            # Le même bailleur ne s'enregistre pas deux fois : on reprend sa fiche.
            self.doublon = donnees.get("nom") and Bailleur.deja_enregistre(
                donnees["type"], donnees["nom"], donnees.get("prenom", ""), donnees.get("siren", ""),
                donnees.get("code_postal", ""), sauf=self.instance.pk,
            )
            if self.doublon:
                self.add_error("nom", f"« {self.doublon} » est déjà enregistré : reprenez sa fiche au lieu d'en créer une autre.")
        return donnees


class AdresseDejaConnueMixin:
    """Avant d'enregistrer une adresse déjà présente, on montre ce qui s'y
    trouve et on demande confirmation : plusieurs lots peuvent légitimement
    partager une adresse, mais c'est aussi la façon dont naît un doublon."""

    def ajouter_confirmation(self):
        self.fields["confirmer_adresse"] = forms.BooleanField(
            required=False, label="Enregistrer quand même : c'est bien une autre propriété",
        )
        # Ce qui existe déjà à l'adresse saisie, pour l'afficher avec la question.
        self.deja_la, self.doublons, self.immeubles_la = [], [], []

    def confirmer(self, donnees):
        if (self.deja_la or self.immeubles_la) and not donnees.get("confirmer_adresse"):
            self.add_error("adresse", "Cette adresse est déjà enregistrée : voir en haut de la page.")


class BailleurForm(SocieteOuPersonneMixin, forms.ModelForm):
    class Meta:
        model = Bailleur
        fields = [
            "type", "nom", "civilite", "prenom", "conjoint_civilite", "conjoint_nom", "conjoint_prenom",
            "representant_civilite", "representant_nom", "representant_prenom", "representant_qualite", "siren",
            "adresse", "code_postal", "ville", "email", "telephone", "versement", "iban",
        ]


class AssociesFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        lignes = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        total = sum(ligne["parts"] for ligne in lignes if ligne.get("parts") is not None)
        if total > 100:
            raise forms.ValidationError(f"Les parts des associés font {total:f} % : elles ne peuvent dépasser 100 %.")


AssociesForms = forms.inlineformset_factory(
    Bailleur, Associe, formset=AssociesFormSet, fields=["civilite", "nom", "prenom", "parts"], extra=2, can_delete=True,
)


class NouveauBailleurForm(SocieteOuPersonneMixin, forms.ModelForm):
    """Création rapide d'un bailleur pendant celle d'une propriété ou d'un
    bail : l'essentiel, le reste se complète sur sa fiche."""

    class Meta:
        model = Bailleur
        fields = [
            "type", "nom", "civilite", "prenom", "representant_civilite", "representant_nom", "representant_prenom",
            "representant_qualite", "adresse", "code_postal", "ville", "email", "telephone",
        ]


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


class ProprieteForm(AdresseDejaConnueMixin, forms.ModelForm):
    def clean_numero_lot(self):
        return self.cleaned_data.get("numero_lot") or None

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
        ("Identification", ["bailleur", "immeuble", "numero_lot", "usage", "meuble", "actif"]),
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
        self.ajouter_confirmation()
        # Un immeuble par ligne, désigné par son nom et son adresse.
        self.fields["immeuble"].queryset = Immeuble.objects.order_by("nom", "ville", "adresse")
        self.fields["immeuble"].label_from_instance = lambda immeuble: immeuble.designation
        self.fields["immeuble"].empty_label = "Aucun (logement isolé)"

    def sections(self):
        return [(titre, [self[nom] for nom in noms]) for titre, noms in self.SECTIONS]

    def clean(self):
        donnees = super().clean()
        mini, maxi = donnees.get("depenses_energie_min"), donnees.get("depenses_energie_max")
        if mini is not None and maxi is not None and mini > maxi:
            self.add_error("depenses_energie_max", "Le maximum doit être supérieur au minimum.")
        self.controler_adresse(donnees)
        return donnees

    def controler_adresse(self, donnees):
        adresse, code_postal = donnees.get("adresse"), donnees.get("code_postal")
        if not adresse or not code_postal:
            return
        instance = self.instance
        if instance.pk and (cle_adresse(instance.adresse), instance.code_postal) == (cle_adresse(adresse), code_postal):
            # Fiche existante dont l'adresse ne change pas : rien à signaler.
            return
        memes = Propriete.a_l_adresse(adresse, code_postal, sauf=instance.pk)
        lot = cle_adresse(donnees.get("complement")), cle_adresse(donnees.get("numero_appartement"))
        self.doublons = [p for p in memes if p.cle_lot == lot]
        if donnees.get("immeuble") and not self.doublons:
            # Nouveau lot d'un immeuble, distinct des autres : c'est attendu.
            return
        self.deja_la = memes
        self.confirmer(donnees)


class ImmeubleForm(AdresseDejaConnueMixin, forms.ModelForm):
    class Meta:
        model = Immeuble
        fields = ["nom", "adresse", "code_postal", "ville"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ajouter_confirmation()
        self.fields["confirmer_adresse"].label = "Créer quand même cet immeuble et ses lots"
        self.doublon = None

    def champs(self):
        return [self[nom] for nom in self.Meta.fields]

    def clean(self):
        donnees = super().clean()
        adresse, code_postal = donnees.get("adresse"), donnees.get("code_postal")
        if not (adresse and code_postal and donnees.get("ville")):
            return donnees
        # Le même immeuble ne s'enregistre pas deux fois, quelle que soit l'écriture de son adresse.
        cle = Immeuble.identite(donnees.get("nom"), adresse, code_postal, donnees["ville"])
        self.doublon = Immeuble.objects.filter(cle=cle).exclude(pk=self.instance.pk).first()
        if self.doublon:
            self.add_error("nom" if donnees.get("nom") else "adresse", "Cet immeuble est déjà enregistré.")
        elif not self.instance.pk:
            # Autre nom à la même adresse (bâtiment A, bâtiment B) : possible, après confirmation.
            self.immeubles_la = Immeuble.a_l_adresse(adresse, code_postal)
            self.deja_la = Propriete.a_l_adresse(adresse, code_postal)
            self.confirmer(donnees)
        return donnees


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


class LotsFormSet(forms.BaseFormSet):
    def clean(self):
        """Deux lignes ne peuvent pas désigner le même lot."""
        vus = set()
        for ligne in self.forms:
            if not ligne.has_changed() or ligne.errors:
                continue
            lot = cle_adresse(ligne.cleaned_data.get("complement")), cle_adresse(ligne.cleaned_data.get("numero_appartement"))
            if lot in vus:
                ligne.add_error("complement", "Ce lot figure déjà dans une autre ligne.")
            vus.add(lot)


LotFormSet = forms.formset_factory(
    LotForm, formset=LotsFormSet, extra=3, min_num=1, validate_min=True, max_num=60,
)


class PieceForm(forms.ModelForm):
    """Dépôt d'un document sur la fiche d'une propriété."""

    TAILLE_MAX = 20 * 1024 * 1024

    class Meta:
        model = Piece
        fields = ["type", "date_document", "libelle", "fichier"]
        widgets = {
            "date_document": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "fichier": forms.FileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}),
        }

    def __init__(self, *args, bailleur=False, **kwargs):
        """Les types proposés dépendent de ce à quoi la pièce s'attache."""
        super().__init__(*args, **kwargs)
        self.fields["type"].choices = [("", "---------")] + [
            (valeur, libelle) for valeur, libelle in Piece.Type.choices
            if (valeur in Piece.DU_BAILLEUR) == bailleur or valeur == Piece.Type.AUTRE
        ]

    def clean_fichier(self):
        return controler_fichier(self.cleaned_data["fichier"], self.TAILLE_MAX)


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
