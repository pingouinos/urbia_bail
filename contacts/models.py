"""Fiche d'une personne ou d'une entreprise en relation avec l'agence.

Le contact est la fiche vivante : identité et coordonnées à jour. Les lignes
qui s'y rattachent (bailleur, locataire d'un bail, candidat, garant) gardent
leurs propres champs, parce qu'elles figent l'identité telle qu'elle figure
sur un mandat ou un bail signé. Les catégories d'un contact se déduisent de
ces liens : il est bailleur parce qu'il a une fiche de bailleur, locataire
parce qu'il a un bail en cours.

Un contact créé à partir d'un candidat, d'un garant ou d'un locataire est
« automatique » : il est effacé quand plus rien ne s'y rattache, pour que la
purge des candidatures non retenues (trois mois) emporte aussi la personne.
"""

import unicodedata
from dataclasses import dataclass

from django.db import models
from django.urls import reverse
from simple_history.models import HistoricalRecords

from core.champs import TelephoneField

from proprietes.models import Horodatage

EN_COURS = ("a_etudier", "retenue")  # statuts d'une candidature qui n'est ni refusée ni abandonnée


def normaliser(texte):
    sans_accents = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return " ".join(sans_accents.lower().split())


def cle_identite(nom, prenom):
    return f"{normaliser(nom)}|{normaliser(prenom)}"[:255]


@dataclass(frozen=True)
class Lien:
    """Ce à quoi une personne est rattachée."""

    type: str  # propriete, bail, candidature ou garantie
    libelle: str
    url: str
    propriete_id: int
    precision: str = ""


class Contact(Horodatage):
    class Nature(models.TextChoices):
        PERSONNE = "personne", "Personne"
        ENTREPRISE = "entreprise", "Entreprise"

    class Civilite(models.TextChoices):
        MONSIEUR = "M.", "Monsieur"
        MADAME = "Mme", "Madame"

    class Categorie(models.TextChoices):
        BAILLEUR = "bailleur", "Propriétaire (bailleur)"
        LOCATAIRE = "locataire", "Locataire"
        GARANT = "garant", "Garant"

    # Étiquettes affichées, dans cet ordre.
    ETIQUETTES = {
        "bailleur": "Bailleur", "locataire": "Locataire", "candidat": "Candidat",
        "ancien": "Ancien locataire", "garant": "Garant", "representant": "Représentant", "associe": "Associé",
    }

    nature = models.CharField(max_length=10, choices=Nature.choices, default=Nature.PERSONNE)
    civilite = models.CharField("civilité", max_length=5, choices=Civilite.choices, blank=True)
    nom = models.CharField("nom ou raison sociale", max_length=200)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    date_naissance = models.DateField("date de naissance", null=True, blank=True)
    lieu_naissance = models.CharField("lieu de naissance", max_length=100, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telephone = TelephoneField("téléphone", blank=True)
    adresse = models.CharField(max_length=255, blank=True)
    code_postal = models.CharField("code postal", max_length=10, blank=True)
    ville = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    categorie = models.CharField(
        "catégorie", max_length=10, choices=Categorie.choices, blank=True,
        help_text="Catégorie indiquée à la création ; les autres se déduisent des propriétés, baux et candidatures.",
    )
    # Nom et prénom sans majuscules ni accents, pour retrouver une personne.
    cle = models.CharField(max_length=255, db_index=True, editable=False)
    automatique = models.BooleanField(default=False, editable=False)

    history = HistoricalRecords()

    class Meta:
        ordering = ["cle", "pk"]
        verbose_name = "contact"

    def __str__(self):
        return self.designation

    def save(self, *args, **kwargs):
        self.cle = cle_identite(self.nom, self.prenom)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("contacts:contact", args=[self.pk])

    @property
    def designation(self):
        if self.nature == self.Nature.ENTREPRISE:
            return self.nom
        return f"{self.nom.upper()} {self.prenom}".strip()

    @property
    def entreprise(self):
        return self.nature == self.Nature.ENTREPRISE

    # --- Rattachement des lignes ------------------------------------------------

    @classmethod
    def rattacher(cls, ligne, automatique=True, synchroniser=False):
        """Contact de la personne décrite par une ligne (bailleur, locataire,
        candidat ou garant). Une ligne déjà rattachée garde son contact ;
        sinon c'est celui qui porte le même nom et le même prénom, sauf dates
        de naissance différentes, ou un nouveau. Les coordonnées manquantes du
        contact sont complétées par celles de la ligne ; avec `synchroniser`,
        la ligne fait foi et les remplace (fiche du bailleur)."""
        naissance = getattr(ligne, "date_naissance", None)
        prenom = getattr(ligne, "prenom", "")
        entreprise = getattr(ligne, "type", "physique") != "physique"
        contact = ligne.contact if ligne.contact_id else None
        if contact is None:
            memes = cls.objects.filter(cle=cle_identite(ligne.nom, prenom))
            contact = next(
                (c for c in memes if not (c.date_naissance and naissance and c.date_naissance != naissance)), None
            )
        if contact is None:
            contact = cls(nom=ligne.nom, prenom=prenom, automatique=automatique)
            if entreprise:
                contact.nature = cls.Nature.ENTREPRISE
        modifie = contact.pk is None
        if synchroniser:
            nature = cls.Nature.ENTREPRISE if entreprise else cls.Nature.PERSONNE
            for champ, valeur in (("nom", ligne.nom), ("prenom", prenom), ("nature", nature)):
                if getattr(contact, champ) != valeur:
                    setattr(contact, champ, valeur)
                    modifie = True
        for champ in ("civilite", "date_naissance", "lieu_naissance", "email", "telephone", "adresse",
                      "code_postal", "ville"):
            valeur = getattr(ligne, champ, None)
            if valeur and (synchroniser or not getattr(contact, champ)) and getattr(contact, champ) != valeur:
                setattr(contact, champ, valeur)
                modifie = True
        if synchroniser and entreprise:
            # Une société n'a ni civilité ni naissance : ce qui en restait d'une saisie ancienne s'efface.
            for champ, vide in (("civilite", ""), ("date_naissance", None), ("lieu_naissance", "")):
                if getattr(contact, champ):
                    setattr(contact, champ, vide)
                    modifie = True
        if contact.automatique and not automatique:
            contact.automatique, modifie = False, True
        if modifie:
            contact.save()
        return contact

    @classmethod
    def effacer_orphelins(cls):
        """Efface les contacts automatiques auxquels plus rien ne se rattache."""
        return cls.objects.filter(
            automatique=True, bailleurs__isnull=True, locataires__isnull=True, candidats__isnull=True,
            garants__isnull=True, societes__isnull=True, associes__isnull=True,
        ).delete()

    # --- Catégories et liens ----------------------------------------------------

    @property
    def codes(self):
        """Catégories déduites des liens, complétées par celle indiquée à la
        création tant qu'aucun lien ne la confirme."""
        codes = set()
        if self.bailleurs.all():
            codes.add("bailleur")
        if self.societes.all():
            codes.add("representant")
        if self.associes.all():
            codes.add("associe")
        baux = [locataire.bail for locataire in self.locataires.all()]
        if any(bail.en_cours for bail in baux):
            codes.add("locataire")
        elif baux:
            codes.add("ancien")
        candidatures = [candidat.candidature for candidat in self.candidats.all()]
        if any(c.statut in EN_COURS and not c.bail_id for c in candidatures):
            codes.add("candidat")
        if any(garant.candidat.candidature.statut in EN_COURS for garant in self.garants.all()):
            codes.add("garant")
        if self.categorie and not (self.categorie == "locataire" and codes & {"ancien", "candidat"}):
            codes.add(self.categorie)
        return codes

    @property
    def etiquettes(self):
        codes = self.codes
        return [(code, libelle) for code, libelle in self.ETIQUETTES.items() if code in codes]

    @property
    def proprietes(self):
        """Propriétés de la personne, et celles des sociétés qu'elle représente ou dont elle est associée."""
        societes = [associe.bailleur for associe in self.associes.all()]
        bailleurs = {b.pk: b for b in [*self.bailleurs.all(), *self.societes.all(), *societes]}
        return [propriete for bailleur in bailleurs.values() for propriete in bailleur.proprietes.all()]

    @property
    def baux(self):
        baux = {locataire.bail for locataire in self.locataires.all()}
        return sorted(baux, key=lambda bail: bail.date_effet, reverse=True)

    @property
    def candidatures(self):
        return list({candidat.candidature for candidat in self.candidats.all()})

    @property
    def garanties(self):
        return list(self.garants.all())

    @property
    def liens(self):
        liens = [
            Lien("societe", f"Représente {societe.nom}", societe.get_absolute_url(), None, societe.representant_qualite)
            for societe in self.societes.all()
        ]
        liens += [
            Lien("societe", f"Associé de {associe.bailleur.nom}", associe.bailleur.get_absolute_url(), None,
                 f"{associe.parts.normalize():f} % des parts".replace(".", ",") if associe.parts is not None else "")
            for associe in self.associes.all()
        ]
        liens += [Lien("propriete", str(p), p.get_absolute_url(), p.pk) for p in self.proprietes]
        liens += [
            Lien("bail", f"Bail · {bail.propriete}", bail.get_absolute_url(), bail.propriete_id,
                 "" if bail.en_cours else "terminé")
            for bail in self.baux
        ]
        liens += [
            Lien("candidature", f"Candidature · {c.propriete}", c.get_absolute_url(), c.propriete_id,
                 c.get_statut_display().lower())
            for c in self.candidatures if c.statut in EN_COURS and not c.bail_id
        ]
        for garant in self.garants.all():
            candidature = garant.candidat.candidature
            if candidature.statut in EN_COURS:
                cible = candidature.bail if candidature.bail_id else candidature
                liens.append(Lien(
                    "garantie", f"Garant de {garant.candidat}", cible.get_absolute_url(), candidature.propriete_id,
                ))
        return liens

    def correspond(self, mots):
        texte = normaliser(" ".join(
            [self.nom, self.prenom, self.email, self.telephone, *(lien.libelle for lien in self.liens)]
        ))
        return all(mot in texte for mot in mots)

    @classmethod
    def avec_liens(cls):
        """Contacts avec tout ce qu'il faut pour afficher catégories et liens."""
        return cls.objects.prefetch_related(
            "bailleurs__proprietes", "bailleurs__representant", "bailleurs__associes__contact", "societes__proprietes",
            "associes__bailleur__proprietes", "locataires__bail__propriete", "candidats__candidature__propriete",
            "garants__candidat__candidature__bail",
        )
