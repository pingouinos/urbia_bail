from django import forms

from .models import Alerte


class ExamenForm(forms.Form):
    statut = forms.ChoiceField(label="Décision", choices=Alerte.Statut.choices)
    commentaire = forms.CharField(
        label="Justification et références", max_length=5000,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Préciser l'impact, la date d'effet vérifiée ou encore inconnue, les références et l'action à mener.",
    )
