import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.agence import agence_active
from core.fichiers import servir

from .forms import SuiviForm, TicketForm
from .models import Ticket

logger = logging.getLogger(__name__)


def appareil(user_agent):
    """« Chrome · ordinateur » : de quoi reproduire un bug, sans garder tout
    l'en-tête du navigateur."""
    navigateur = next(
        (nom for marque, nom in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
         if marque in user_agent),
        "navigateur inconnu",
    )
    if "iPad" in user_agent or "Tablet" in user_agent:
        support = "tablette"
    elif "Mobi" in user_agent:
        support = "téléphone"
    else:
        support = "ordinateur"
    return f"{navigateur} · {support}"


def mes_tickets(request):
    """Ouvrir un ticket, et suivre ceux qu'on a ouverts."""
    if request.method == "POST":
        form = TicketForm(request.POST, request.FILES)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.auteur = request.user
            ticket.appareil = appareil(request.META.get("HTTP_USER_AGENT", ""))
            ticket.save()
            prevenir_exploitant(request, ticket)
            messages.success(request, "Ticket envoyé. La réponse s'affichera ici.")
            return redirect("tickets:liste")
    else:
        page = request.GET.get("page", "")
        form = TicketForm(initial={"page": "" if page.startswith(reverse("tickets:liste")) else page})
    tickets = Ticket.objects.filter(auteur=request.user)
    return render(request, "tickets/mes_tickets.html", {"form": form, "tickets": tickets})


def capture(request, pk):
    ticket = get_object_or_404(Ticket.objects.filter(auteur=request.user), pk=pk)
    return servir(ticket.capture)


def prevenir_exploitant(request, ticket):
    """E-mail aux superutilisateurs. Un envoi raté ne perd pas le ticket :
    il reste dans la Plateforme."""
    destinataires = list(
        get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(email="").values_list("email", flat=True)
    )
    if not destinataires:
        return
    agence = agence_active()
    auteur = request.user.get_full_name() or request.user.username
    corps = (
        f"{auteur} ({agence.designation if agence else 'sans agence'}) a ouvert un ticket :\n\n{ticket.texte}\n\n"
        f"Page : {ticket.page or 'non indiquée'}\nAppareil : {ticket.appareil}\n"
        f"{'Capture jointe dans la Plateforme.' if ticket.capture else ''}\n\n"
        f"Suivre le ticket : {request.build_absolute_uri(ticket.get_absolute_url())}\n"
    )
    try:
        EmailMessage(
            subject=f"[Ticket] {ticket.get_type_display()} : {ticket.titre}", body=corps, to=destinataires,
        ).send()
    except Exception:
        logger.exception("E-mail du ticket %s non envoyé", ticket.pk)


# Espace Plateforme : l'exploitant n'a pas d'agence, il lit les tickets de
# toutes les agences par le manager `tous`.

def _exploitant(request):
    if not request.user.is_superuser:
        raise PermissionDenied


def lien_issue(ticket):
    """Formulaire « Nouvelle issue » de GitHub prérempli, que l'exploitant
    relit avant de le valider. Ni capture, ni nom d'auteur : le dépôt est public."""
    if not settings.TICKETS_DEPOT_GITHUB:
        return ""
    corps = (
        f"{ticket.texte}\n\n---\nTicket n° {ticket.pk} ({ticket.get_type_display().lower()}) · "
        f"page {ticket.page or 'non indiquée'} · {ticket.appareil}"
    )
    parametres = urlencode({"title": ticket.titre, "body": corps})
    return f"https://github.com/{settings.TICKETS_DEPOT_GITHUB}/issues/new?{parametres}"


def plateforme_tickets(request):
    _exploitant(request)
    tous = request.GET.get("etat") == "tous"
    tickets = Ticket.tous.select_related("agence", "auteur")
    a_traiter = tickets.exclude(etat=Ticket.Etat.RESOLU)
    return render(request, "tickets/plateforme_tickets.html", {
        "tickets": tickets if tous else a_traiter,
        "tous": tous,
        "nb_a_traiter": a_traiter.count(),
        "nb_tickets": tickets.count(),
    })


def plateforme_ticket(request, pk):
    _exploitant(request)
    ticket = get_object_or_404(Ticket.tous.select_related("agence", "auteur"), pk=pk)
    reponse_avant = ticket.reponse
    form = SuiviForm(request.POST or None, instance=ticket)
    if form.is_valid():
        ticket = form.save(commit=False)
        if ticket.reponse != reponse_avant:
            ticket.repondu_le = timezone.now() if ticket.reponse else None
        ticket.save()
        messages.success(request, "Ticket mis à jour.")
        return redirect("plateforme_tickets")
    return render(request, "tickets/plateforme_ticket.html", {
        "ticket": ticket, "form": form, "lien_issue": lien_issue(ticket),
    })


def plateforme_ticket_capture(request, pk):
    _exploitant(request)
    return servir(get_object_or_404(Ticket.tous, pk=pk).capture)
