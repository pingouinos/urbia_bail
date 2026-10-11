"""Carnet des personnes en relation avec l'agence.

Bailleurs, candidats et locataires restent dans leurs tables : cette vue les
réunit. Une personne n'y figure qu'une fois, même si elle apparaît dans
plusieurs tables ou plusieurs fois dans la même (un locataire qui a signé deux
baux, un bailleur qui est aussi locataire) : ses catégories se cumulent et ses
liens (propriétés, baux, candidatures) se listent sous son nom.

Deux enregistrements désignent la même personne quand ils portent le même nom
et le même prénom, sans tenir compte des majuscules ni des accents, et qu'ils
n'ont pas deux dates de naissance différentes.
"""

import unicodedata
from dataclasses import dataclass, field

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from candidatures.models import Candidat, Candidature
from contrats.models import Locataire
from proprietes.models import Bailleur, Propriete

# Dans l'ordre d'affichage des étiquettes.
CATEGORIES = [
    ("bailleur", "Bailleurs"), ("locataire", "Locataires"), ("candidat", "Candidats"),
    ("ancien", "Anciens locataires"),
]
LIBELLES = {"bailleur": "Bailleur", "locataire": "Locataire", "candidat": "Candidat", "ancien": "Ancien locataire"}
EN_COURS = (Candidature.Statut.A_ETUDIER, Candidature.Statut.RETENUE)


def normaliser(texte):
    sans_accents = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return " ".join(sans_accents.lower().split())


@dataclass(frozen=True)
class Lien:
    """Ce à quoi une personne est rattachée."""

    type: str  # propriete, bail ou candidature
    libelle: str
    url: str
    propriete_id: int
    precision: str = ""


@dataclass
class Contact:
    nom: str
    prenom: str = ""
    naissance: object = None
    email: str = ""
    telephone: str = ""
    url: str = ""
    categories: list = field(default_factory=list)
    liens: list = field(default_factory=list)

    @property
    def designation(self):
        return f"{self.nom.upper()} {self.prenom}".strip()

    @property
    def etiquettes(self):
        """Catégories à afficher : un locataire en place n'est pas en plus
        « ancien locataire » pour un bail antérieur."""
        codes = [code for code, _ in CATEGORIES if code in self.categories]
        if "locataire" in codes and "ancien" in codes:
            codes.remove("ancien")
        return [(code, LIBELLES[code]) for code in codes]

    @property
    def proprietes(self):
        return [lien for lien in self.liens if lien.type == "propriete"]

    def correspond(self, mots):
        texte = normaliser(" ".join(
            [self.nom, self.prenom, self.email, self.telephone, *(lien.libelle for lien in self.liens)]
        ))
        return all(mot in texte for mot in mots)

    def absorbe(self, autre):
        self.naissance = self.naissance or autre.naissance
        self.email = self.email or autre.email
        self.telephone = self.telephone or autre.telephone
        # La fiche du bailleur, quand elle existe, est la page de la personne.
        if "bailleur" in autre.categories and "bailleur" not in self.categories:
            self.url = autre.url
        self.categories += [code for code in autre.categories if code not in self.categories]
        self.liens += [lien for lien in autre.liens if lien not in self.liens]


def _enregistrements():
    """Une entrée par ligne des tables de personnes, avant regroupement."""
    for bailleur in Bailleur.objects.prefetch_related("proprietes"):
        yield Contact(
            bailleur.nom, bailleur.prenom, None, bailleur.email, bailleur.telephone, bailleur.get_absolute_url(),
            ["bailleur"],
            [Lien("propriete", str(propriete), propriete.get_absolute_url(), propriete.pk)
             for propriete in bailleur.proprietes.all()],
        )
    locataires = Locataire.objects.select_related("bail__propriete").order_by("-bail__date_effet")
    for locataire in locataires:
        bail = locataire.bail
        yield Contact(
            locataire.nom, locataire.prenom, locataire.date_naissance, locataire.email, locataire.telephone,
            bail.get_absolute_url(), ["locataire" if bail.en_cours else "ancien"],
            [Lien("bail", f"Bail · {bail.propriete}", bail.get_absolute_url(), bail.propriete_id,
                  "" if bail.en_cours else "terminé")],
        )
    candidats = Candidat.objects.filter(
        candidature__statut__in=EN_COURS, candidature__bail__isnull=True,
    ).select_related("candidature__propriete")
    for candidat in candidats:
        candidature = candidat.candidature
        yield Contact(
            candidat.nom, candidat.prenom, candidat.date_naissance, candidat.email, candidat.telephone,
            candidature.get_absolute_url(), ["candidat"],
            [Lien("candidature", f"Candidature · {candidature.propriete}", candidature.get_absolute_url(),
                  candidature.propriete_id, candidature.get_statut_display().lower())],
        )


def contacts():
    """Les personnes, sans doublon, triées par nom."""
    personnes = {}
    for entree in _enregistrements():
        homonymes = personnes.setdefault((normaliser(entree.nom), normaliser(entree.prenom)), [])
        # Même personne, sauf si deux dates de naissance connues diffèrent.
        meme = next((p for p in homonymes
                     if not (p.naissance and entree.naissance and p.naissance != entree.naissance)), None)
        if meme:
            meme.absorbe(entree)
        else:
            homonymes.append(entree)
    return sorted(
        (personne for homonymes in personnes.values() for personne in homonymes),
        key=lambda personne: (normaliser(personne.nom), normaliser(personne.prenom)),
    )


def liste(request):
    categorie = request.GET.get("categorie")
    mots = normaliser(request.GET.get("q", "")).split()
    propriete = None
    if request.GET.get("propriete", "").isdigit():
        propriete = get_object_or_404(Propriete, pk=request.GET["propriete"])
    personnes = [
        personne for personne in contacts()
        if personne.correspond(mots)
        and (categorie not in LIBELLES or categorie in dict(personne.etiquettes))
        and (propriete is None or any(lien.propriete_id == propriete.pk for lien in personne.liens))
    ]
    if request.GET.get("tri") == "proprietes":
        personnes.sort(key=lambda personne: -len(personne.proprietes))
    page = Paginator(personnes, 50).get_page(request.GET.get("page"))
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return render(request, "contacts/liste.html", {
        "page": page, "parametres": parametres.urlencode(), "categories": CATEGORIES, "propriete": propriete,
    })
