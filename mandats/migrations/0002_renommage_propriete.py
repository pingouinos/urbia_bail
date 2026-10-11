from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("mandats", "0001_initial"),
        ("proprietes", "0005_renommage_propriete"),
    ]

    operations = [
        migrations.RenameField(model_name="mandat", old_name="bien", new_name="propriete"),
        migrations.RenameField(model_name="historicalmandat", old_name="bien", new_name="propriete"),
    ]
