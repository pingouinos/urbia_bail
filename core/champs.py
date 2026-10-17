"""Champs de modèle contrôlés, communs aux fiches : téléphone, code postal
français, SIREN. Le contrôle vit dans le champ pour valoir dans tous les
formulaires, y compris celui que remplit le candidat."""

import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

code_postal_francais = RegexValidator(r"^\d{5}$", "Le code postal compte cinq chiffres.")


def normaliser_telephone(valeur):
    """« 0612345678 », « 06.12.34.56.78 » ou « +33 6 12 34 56 78 » deviennent
    « 06 12 34 56 78 ». Un numéro étranger garde son indicatif : « +32470123456 »."""
    numero = re.sub(r"[\s.\-()]", "", valeur or "")
    if not numero:
        return ""
    numero = re.sub(r"^(\+33|0033)0?", "0", numero)
    if re.fullmatch(r"0[1-9]\d{8}", numero):
        return " ".join(numero[i:i + 2] for i in range(0, 10, 2))
    numero = re.sub(r"^00", "+", numero)
    if re.fullmatch(r"\+\d{8,15}", numero):
        return numero
    raise ValidationError(
        "Numéro de téléphone incorrect : dix chiffres (06 12 34 56 78), ou l'indicatif du pays pour un numéro étranger (+32…)."
    )


class TelephoneField(models.CharField):
    """Numéro de téléphone, contrôlé et mis en forme à l'enregistrement."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 30)
        super().__init__(*args, **kwargs)

    def to_python(self, valeur):
        valeur = super().to_python(valeur)
        return normaliser_telephone(valeur) if valeur else valeur

    def formfield(self, **kwargs):
        kwargs.setdefault("widget", forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}))
        return super().formfield(**kwargs)


def siren_valide(valeur):
    """SIREN (9 chiffres) ou SIRET (14 chiffres), espaces admis, avec la clé de contrôle."""
    chiffres = re.sub(r"\s", "", valeur or "")
    if not re.fullmatch(r"\d{9}|\d{14}", chiffres):
        raise ValidationError("Un SIREN compte 9 chiffres (14 pour un SIRET).")
    total = 0
    for rang, chiffre in enumerate(reversed(chiffres)):
        nombre = int(chiffre) * (2 if rang % 2 else 1)
        total += nombre - 9 if nombre > 9 else nombre
    if total % 10:
        raise ValidationError("Ce numéro SIREN ne passe pas le contrôle : vérifier les chiffres saisis.")


def iban_valide(valeur):
    """IBAN, espaces admis, avec sa clé de contrôle (modulo 97)."""
    iban = re.sub(r"\s", "", valeur or "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        raise ValidationError("Un IBAN commence par deux lettres et deux chiffres (FR76…).")
    if int("".join(str(int(c, 36)) for c in iban[4:] + iban[:4])) % 97 != 1:
        raise ValidationError("Cet IBAN ne passe pas le contrôle : vérifier les caractères saisis.")
