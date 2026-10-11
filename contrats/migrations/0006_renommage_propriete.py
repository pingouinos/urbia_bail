from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("contrats", "0005_mise_en_location"),
        ("proprietes", "0005_renommage_propriete"),
    ]

    operations = [
        migrations.RenameField(model_name="bail", old_name="bien", new_name="propriete"),
        migrations.RenameField(model_name="historicalbail", old_name="bien", new_name="propriete"),
    ]
