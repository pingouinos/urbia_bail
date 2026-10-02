"""Rôles de l'application, portés par des groupes Django."""

ADMINISTRATEURS = "Administrateurs"
GESTIONNAIRES = "Gestionnaires"
ROLES = (ADMINISTRATEURS, GESTIONNAIRES)


def est_administrateur(user):
    return user.is_superuser or user.groups.filter(name=ADMINISTRATEURS).exists()


def attribuer_role(user, role):
    """Place l'utilisateur dans un seul rôle et aligne l'accès à l'administration."""
    from django.contrib.auth.models import Group

    if role not in ROLES:
        raise ValueError(f"Rôle inconnu : {role}")
    user.groups.remove(*Group.objects.filter(name__in=ROLES).exclude(name=role))
    user.groups.add(Group.objects.get(name=role))
    user.is_staff = role == ADMINISTRATEURS
    user.save(update_fields=["is_staff"])
