import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def rattacher(apps, schema_editor):
    """Tous les comptes existants appartiennent à l'agence déjà en service."""
    Organisation = apps.get_model("core", "Organisation")
    Membre = apps.get_model("core", "Membre")
    Utilisateur = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    comptes = Utilisateur.objects.filter(membre__isnull=True)
    if not comptes.exists():
        return
    agence = Organisation.objects.order_by("pk").first() or Organisation.objects.create()
    Membre.objects.bulk_create(Membre(utilisateur=compte, organisation=agence) for compte in comptes)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_mentions_legales"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Membre",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "organisation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="membres",
                        to="core.organisation",
                        verbose_name="agence",
                    ),
                ),
                (
                    "utilisateur",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="membre",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "membre",
            },
        ),
        migrations.RunPython(rattacher, migrations.RunPython.noop),
    ]
