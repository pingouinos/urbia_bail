from django.db import migrations

ROLES = ("Administrateurs", "Gestionnaires")


def creer_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nom in ROLES:
        Group.objects.get_or_create(name=nom)


def supprimer_roles(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [("auth", "0012_alter_user_first_name_max_length")]

    operations = [migrations.RunPython(creer_roles, supprimer_roles)]
