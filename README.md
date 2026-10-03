# UrbiaBail

Application web interne de l'agence pour gérer le référentiel des biens, les candidatures (lien DossierFacile) et la génération des baux.

Le cadrage fonctionnel et les décisions sont dans le document de cadrage partagé dans le projet.

## Stack

- Django 5.2 (Python 3.12), PostgreSQL 16
- Comptes propres à l'application avec double authentification (TOTP) obligatoire, verrouillage après 5 échecs ; connexion Google Workspace prévue en 2027
- Gotenberg pour la conversion Word vers PDF
- Docker Compose sur un VPS OVH, Caddy pour le HTTPS automatique

## Rôles

| Rôle | Accès |
| --- | --- |
| Administrateurs | Tout, y compris l'administration (comptes, modèles, paramètres) |
| Gestionnaires | Biens, candidatures, baux |

Un administrateur crée les comptes dans l'administration (`/admin/`) et place chaque collaborateur dans un groupe. À la première connexion, le collaborateur scanne un QR code avec une application d'authentification (Google Authenticator, Microsoft Authenticator…) ; chaque connexion demande ensuite le code à 6 chiffres. Un administrateur peut réinitialiser l'appareil d'un collaborateur dans l'administration (« TOTP devices »).

La connexion par un annuaire LDAP (NAS Synology) reste disponible mais désactivée (`LDAP_ENABLED`).

## Développement local

```bash
sudo apt install libldap2-dev libsasl2-dev   # nécessaires à python-ldap
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
4. Cloner le dépôt dans `/opt/urbiabail`, copier `.env.example` en `.env` et le remplir (`SECRET_KEY`, `POSTGRES_PASSWORD`, `DOMAINE`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`).
5. Lancer la pile : `docker compose up -d --build`. Caddy obtient seul le certificat HTTPS.
6. Créer le premier administrateur : `docker compose exec web python manage.py createsuperuser`, puis le placer dans le groupe « Administrateurs ».

La sonde `/sante/` répond `{"statut": "ok"}` quand l'application et la base fonctionnent.

Mise à jour : `git pull && docker compose up -d --build` (les migrations s'appliquent au démarrage).

### Sauvegarde

`scripts/sauvegarde.sh` exporte chaque nuit la base et les documents dans `/var/backups/urbiabail` et garde 14 jours. À planifier par cron (`0 2 * * * /opt/urbiabail/scripts/sauvegarde.sh`) puis à recopier hors du VPS, par exemple vers le NAS de l'agence avec Hyper Backup ou vers un stockage objet OVH.

Restauration de la base :

```bash
docker compose exec -T db pg_restore -U urbiabail -d urbiabail --clean < base-AAAA-MM-JJ.dump
```
