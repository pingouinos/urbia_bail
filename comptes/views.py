"""Double authentification par code TOTP (Google Authenticator, Microsoft
Authenticator, FreeOTP...)."""

from io import BytesIO

import qrcode
import qrcode.image.svg
from django import forms
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice


class CodeForm(forms.Form):
    code = forms.CharField(
        label="Code à 6 chiffres",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True}
        ),
    )


def _qr_svg(url):
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    flux = BytesIO()
    image.save(flux)
    return flux.getvalue().decode()


def _suite(request):
    suite = request.POST.get("next") or request.GET.get("next") or "/"
    if url_has_allowed_host_and_scheme(suite, {request.get_host()}, request.is_secure()):
        return suite
    return "/"


def mfa(request):
    """Saisie du code ; à la première connexion, enrôlement d'un appareil."""
    if request.user.is_verified():
        return redirect(_suite(request))

    appareil = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    enrolement = appareil is None
    if enrolement:
        appareil, _ = TOTPDevice.objects.get_or_create(
            user=request.user, confirmed=False, defaults={"name": "Application d'authentification"}
        )

    form = CodeForm(request.POST or None)
    if form.is_valid():
        if appareil.verify_token(form.cleaned_data["code"]):
            if enrolement:
                appareil.confirmed = True
                appareil.save(update_fields=["confirmed"])
            otp_login(request, appareil)
            return redirect(_suite(request))
        form.add_error("code", "Code incorrect ou expiré.")

    contexte = {"form": form, "enrolement": enrolement, "next": _suite(request)}
    if enrolement:
        contexte["qr_svg"] = _qr_svg(appareil.config_url)
    return render(request, "comptes/mfa.html", contexte)
