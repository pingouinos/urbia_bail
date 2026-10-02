"""Configuration Django d'UrbiaBail.

Toute la configuration propre à un déploiement passe par des variables
d'environnement (fichier .env en local, voir .env.example).
"""

import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
    LDAP_ENABLED=(bool, False),
    LDAP_START_TLS=(bool, False),
    LDAP_GROUP_TYPE=(str, "ad"),
    SECURE_COOKIES=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY", default="")
if not SECRET_KEY:
    if not (DEBUG or "test" in sys.argv):
        raise ImproperlyConfigured("SECRET_KEY doit être défini (voir .env.example).")
    SECRET_KEY = "dev-uniquement"
# localhost reste autorisé pour la sonde de santé lancée dans le conteneur.
ALLOWED_HOSTS = [*env("ALLOWED_HOSTS"), "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "comptes",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "comptes.middleware.ConnexionObligatoireMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Base de données : PostgreSQL en production (DATABASE_URL), SQLite en secours
# pour un premier essai local.
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Fichiers statiques versionnés en production (collectstatic dans l'image
    # Docker), stockage simple en développement et pendant les tests.
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if env.bool("STATIC_MANIFEST", default=False)
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

# Dossier où l'application range les documents (partage du NAS monté
# dans le conteneur en production).
MEDIA_ROOT = Path(env("DOCUMENTS_DIR", default=str(BASE_DIR / "documents")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "connexion"
LOGIN_REDIRECT_URL = "accueil"
LOGOUT_REDIRECT_URL = "connexion"

SESSION_COOKIE_SECURE = env("SECURE_COOKIES")
CSRF_COOKIE_SECURE = env("SECURE_COOKIES")
SESSION_COOKIE_AGE = 60 * 60 * 10  # une journée de travail

# Service de conversion Word vers PDF (utilisé à l'étape « Baux »).
GOTENBERG_URL = env("GOTENBERG_URL", default="http://gotenberg:3000")

# --- Authentification -------------------------------------------------------
# Les comptes locaux Django restent possibles (administrateur de secours).
# Quand LDAP_ENABLED est vrai, les collaborateurs se connectent avec leur
# compte de l'annuaire Synology.

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

LDAP_ENABLED = env("LDAP_ENABLED")
if LDAP_ENABLED:
    from comptes.ldap import configurer_ldap

    globals().update(configurer_ldap(env))
    AUTHENTICATION_BACKENDS.insert(0, "django_auth_ldap.backend.LDAPBackend")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "django_auth_ldap": {"handlers": ["console"], "level": "INFO"},
    },
}
