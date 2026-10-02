# UrbiaBail

Application web interne de l'agence pour gérer le référentiel des biens, les candidatures (lien DossierFacile) et la génération des baux.

Le cadrage fonctionnel et les décisions sont dans le document de cadrage partagé dans le projet.

## Stack

- Django 5.2 (Python 3.12), PostgreSQL 16
- Connexion par l'annuaire du NAS Synology (LDAP), puis Google Workspace
- Gotenberg pour la conversion Word vers PDF
- Docker Compose, hébergé sur le NAS Synology (Container Manager)

## Rôles

| Rôle | Accès |
| --- | --- |
| Administrateurs | Tout, y compris l'administration (comptes, modèles, paramètres) |
| Gestionnaires | Biens, candidatures, baux |

Avec l'annuaire activé, le rôle suit les groupes de l'annuaire : `LDAP_GROUPE_ACCES` donne l'accès, `LDAP_GROUPE_ADMIN` fait d'un collaborateur un administrateur. Le rôle est recalculé à chaque connexion.

## Développement local

```bash
sudo apt install libldap2-dev libsasl2-dev   # nécessaires à python-ldap
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
echo "DEBUG=true" > .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Sans `DATABASE_URL`, l'application utilise SQLite. Les tests : `python manage.py test`.

## Déploiement sur le NAS Synology

1. Installer **Container Manager** depuis le Centre de paquets.
2. Créer un dossier partagé `UrbiaBail` (documents) et copier le dépôt dans `/volume1/docker/urbiabail`.
3. Copier `.env.example` en `.env` et le remplir : `SECRET_KEY`, `POSTGRES_PASSWORD`, `ALLOWED_HOSTS`, chemins des dossiers.
4. Dans l'annuaire, créer un compte de service en lecture seule et deux groupes (accès et administrateurs), puis renseigner les variables `LDAP_*` et passer `LDAP_ENABLED=true`.
   - Paquet « Synology Directory Server » (domaine compatible Active Directory) : `LDAP_GROUP_TYPE=ad`.
   - Paquet « LDAP Server » : `LDAP_GROUP_TYPE=posix`.
5. Container Manager > Projet > Créer, source : `/volume1/docker/urbiabail`, fichier `docker-compose.yml`.
6. Créer un compte administrateur de secours :
   `docker compose exec web python manage.py createsuperuser`
7. Pour le HTTPS : Panneau de configuration > Portail de connexion > Avancé > Proxy inversé, vers `localhost:8000`, puis `SECURE_COOKIES=true`.

La sonde `/sante/` répond `{"statut": "ok"}` quand l'application et la base fonctionnent.

### Sauvegarde

Hyper Backup doit inclure le dossier des documents et celui de la base (`DB_HOST_DIR`). Pour un export logique en plus :

```bash
docker compose exec db pg_dump -U urbiabail urbiabail > urbiabail-$(date +%F).sql
```
