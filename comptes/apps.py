from django.apps import AppConfig


class ComptesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "comptes"
    verbose_name = "Comptes et rôles"

    def ready(self):
        from . import signals  # noqa: F401
