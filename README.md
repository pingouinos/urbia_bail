# UrbiaBail

Application web interne de l'agence pour gérer le référentiel des biens, les candidatures (lien DossierFacile) et la génération des baux.

Le cadrage fonctionnel et les décisions sont dans le document de cadrage partagé dans le projet.

## Stack

- Django 5.2 (Python 3.12), PostgreSQL 16
- Connexion « Se connecter avec Google », limitée au domaine Google Workspace de l'agence ; comptes locaux de secours avec double authentification TOTP et verrouillage après 5 échecs
- Gotenberg pour la conversion Word vers PDF
- Docker Compose sur un VPS OVH, Caddy pour le HTTPS automatique

## Rôles

| Rôle | Accès |
| --- | --- |
| Administrateurs | Tout, y compris l'administration (comptes, modèles, paramètres) |
| Gestionnaires | Biens, candidatures, baux |

Les collaborateurs se connectent avec leur compte Google Workspace de l'agence. La double authentification est alors celle de Google : l'imposer dans la console d'administration Workspace (Sécurité > Validation en deux étapes). Un compte d'un autre domaine, ou dont l'adresse n'est pas vérifiée, est refusé.

Par défaut (`GOOGLE_CREATION_AUTO=false`), un administrateur crée d'abord le compte dans `/admin/` avec l'adresse e-mail Google du collaborateur et le place dans un groupe. Désactiver un compte dans l'administration coupe son accès immédiatement.

Les comptes locaux (identifiant et mot de passe) servent d'accès de secours aux administrateurs. Ils passent par une double authentification TOTP : à la première connexion, scanner le QR code avec une application d'authentification, puis saisir le code à chaque connexion.

## Développement local

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
echo "DEBUG=true" > .env
echo "MFA_OBLIGATOIRE=false" >> .env          # facultatif en local
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Sans `DATABASE_URL`, l'application utilise SQLite. Les tests : `python manage.py test`.

## Déploiement sur un VPS OVH

1. Commander un VPS (Debian 12, 2 Go de RAM minimum, 4 Go conseillés avec Gotenberg) dans un centre de données français.
2. Faire pointer un nom de domaine (enregistrement DNS A) vers l'adresse IP du VPS.
3. Sur le VPS : installer Docker (`curl -fsSL https://get.docker.com | sh`), ouvrir uniquement les ports 22, 80 et 443 dans le pare-feu.
4. Dans la console Google Cloud (projet rattaché au Workspace de l'agence) : écran de consentement OAuth de type « Interne », puis identifiants OAuth « Application Web » avec l'URI de redirection `https://<DOMAINE>/oidc/callback/`.
5. Cloner le dépôt dans `/opt/urbiabail`, copier `.env.example` en `.env` et le remplir (`SECRET_KEY`, `POSTGRES_PASSWORD`, `DOMAINE`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `GOOGLE_*`).
6. Lancer la pile : `docker compose up -d --build`. Caddy obtient seul le certificat HTTPS.
7. Créer l'administrateur de secours : `docker compose exec web python manage.py createsuperuser`, puis, depuis `/admin/`, créer les comptes des collaborateurs avec leur adresse Google.

La sonde `/sante/` répond `{"statut": "ok"}` quand l'application et la base fonctionnent.

Mise à jour : `git pull && docker compose up -d --build` (les migrations s'appliquent au démarrage).

### Sauvegarde

`scripts/sauvegarde.sh` exporte chaque nuit la base et les documents dans `/var/backups/urbiabail` et garde 14 jours. À planifier par cron (`0 2 * * * /opt/urbiabail/scripts/sauvegarde.sh`) puis à recopier hors du VPS, par exemple vers le NAS de l'agence avec Hyper Backup ou vers un stockage objet OVH.

Restauration de la base :

```bash
docker compose exec -T db pg_restore -U urbiabail -d urbiabail --clean < base-AAAA-MM-JJ.dump
```

## Test sur le NAS de l'agence

Une version allégée tourne sur le NAS Synology pour un essai sur le réseau local : voir [deploy/nas/README.md](deploy/nas/README.md).
