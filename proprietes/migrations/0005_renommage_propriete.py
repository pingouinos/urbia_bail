from django.db import migrations


def renommer_permissions(application, renommages):
    """Les permissions suivent le nouveau nom du modèle, pour ne pas perdre
    celles qui auraient été attribuées dans l'administration."""

    def avancer(apps, schema_editor):
        Permission = apps.get_model("auth", "Permission")
        for ancien, nouveau in renommages.items():
            for permission in Permission.objects.filter(
                content_type__app_label=application, content_type__model=nouveau, codename__endswith=f"_{ancien}",
            ):
                permission.codename = permission.codename[: -len(ancien)] + nouveau
                permission.save(update_fields=["codename"])

    return migrations.RunPython(avancer, migrations.RunPython.noop)


class Migration(migrations.Migration):
    """« Bien » devient « Propriete ». Les migrations des autres applications
    qui désignent encore proprietes.bien doivent être passées avant."""

    dependencies = [
        ("proprietes", "0004_mise_en_location"),
        ("mandats", "0001_initial"),
        ("contrats", "0005_mise_en_location"),
        ("candidatures", "0004_nombre_locataires"),
    ]

    operations = [
        migrations.RenameModel(old_name="Bien", new_name="Propriete"),
        migrations.RenameModel(old_name="HistoricalBien", new_name="HistoricalPropriete"),
        renommer_permissions("proprietes", {"bien": "propriete", "historicalbien": "historicalpropriete"}),
    ]
