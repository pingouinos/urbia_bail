# Urbia Gestion

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

## Mandats de gestion

Chaque mandat reçoit à l'enregistrement un numéro d'ordre définitif : la page « Mandats » tient le registre exigé par la loi Hoguet, exportable en Excel, et aucun mandat ne peut être supprimé. Un mandat se crée depuis la fiche d'un bien, prérempli avec le bailleur et le bien, puis se télécharge en Word ou en PDF. L'exemplaire signé et scanné se dépose sur la fiche du mandat.

L'application signale les honoraires de location contraires à l'article 5 I de la loi du 6 juillet 1989 : part du locataire supérieure à celle du bailleur, ou au plafond de 10 €/m² en zone tendue (8 €/m² ailleurs) pour la visite, le dossier et le bail, et de 3 €/m² pour l'état des lieux.

## Candidatures

Une candidature s'enregistre depuis la fiche d'un logement : lien de partage DossierFacile (les pièces justificatives restent sur DossierFacile), garantie, date d'entrée souhaitée, puis jusqu'à quatre candidats avec leur activité et leurs revenus nets. La fiche calcule le taux d'effort sur le loyer charges comprises et signale une caution demandée alors que le mandat prévoit une assurance loyers impayés (art. 22-1 de la loi du 6 juillet 1989). Elle ne contient ni coordonnées bancaires, ni situation de famille, ni nationalité.

À l'enregistrement, le nom et une adresse e-mail suffisent. Le candidat complète lui-même son identité, ses coordonnées, son activité, ses revenus, ses garants et son lien DossierFacile : depuis la fiche, « Envoyer le lien au candidat » prépare un lien personnel, valable 14 jours et utilisable une fois, à envoyer dès l'étude de la candidature ou une fois celle-ci retenue.

La décision (retenue, non retenue, désistement) est datée et attribuée au collaborateur qui l'a prise. Une candidature retenue donne un bail en un clic, prérempli avec les candidats comme locataires. Pour une candidature non retenue, l'application prépare une réponse neutre, sans motif, qui annonce l'effacement des données sous trois mois ; elle l'envoie elle-même si `EMAIL_URL` est renseigné, sinon on la copie dans Gmail et on la note comme envoyée.

Les candidatures non retenues, abandonnées, retenues sans bail ou restées sans suite sont effacées trois mois après la décision (ou la dernière modification), comme le recommande le référentiel CNIL de la gestion locative. La purge passe à chaque ouverture de la liste des candidatures et chaque nuit avec la sauvegarde (`python manage.py purger_candidatures`). Un bouton efface une candidature sur-le-champ, à la demande du candidat.

## Baux d'habitation

Un bail se rédige depuis la fiche d'un logement : il reprend la fiche du bien et les honoraires du mandat en cours, puis on saisit les locataires (jusqu'à quatre), la date de prise d'effet, le loyer, les charges, le dépôt de garantie et l'IRL de référence. Le document suit le contrat type du décret n° 2015-587 (annexe 1 pour le logement nu, annexe 2 pour le meublé, y compris le bail étudiant de neuf mois), complété des clauses propres à l'agence et de la liste des réparations locatives. La durée (3 ou 6 ans en nu selon le bailleur, 1 an en meublé), la clause de solidarité et la liste des annexes s'adaptent toutes seules.

Avant signature, la fiche du bail signale : logement classé G, fiche du bien incomplète, absence de mandat, dépôt de garantie supérieur au maximum légal, loyer supérieur à celui du précédent locataire en zone tendue ou pour un logement classé F ou G, honoraires non conformes.

## Modèles Word

Les documents sont produits à partir des modèles Word de l'agence (`documents/modeles/`), dont les parties variables sont écrites entre doubles accolades, par exemple `{{ mandant }}`. Un administrateur peut télécharger un modèle, le retoucher dans Word et le redéposer depuis la page « Modèles Word » de l'accueil ; l'application refuse un modèle qui contient une balise inconnue.

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
5. Cloner le dépôt dans `/opt/urbiabail`, copier `.env.example` en `.env` et le remplir (`SECRET_KEY`, `POSTGRES_PASSWORD`, `DOMAINE`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `GOOGLE_*`, et `EMAIL_URL` pour que l'application envoie elle-même les réponses aux candidats).
6. Lancer la pile : `docker compose up -d --build`. Caddy obtient seul le certificat HTTPS.
7. Créer l'administrateur de secours : `docker compose exec web python manage.py createsuperuser`, puis, depuis `/admin/`, créer les comptes des collaborateurs avec leur adresse Google.

La sonde `/sante/` répond `{"statut": "ok"}` quand l'application et la base fonctionnent.

Mise à jour : `git pull && docker compose up -d --build` (les migrations s'appliquent au démarrage).

### Sauvegarde

`scripts/sauvegarde.sh` efface les candidatures arrivées à échéance puis exporte chaque nuit la base et les documents dans `/var/backups/urbiabail` et garde 14 jours. À planifier par cron (`0 2 * * * /opt/urbiabail/scripts/sauvegarde.sh`) puis à recopier hors du VPS, par exemple vers le NAS de l'agence avec Hyper Backup ou vers un stockage objet OVH.

Restauration de la base :

```bash
docker compose exec -T db pg_restore -U urbiabail -d urbiabail --clean < base-AAAA-MM-JJ.dump
```

## Test sur le NAS de l'agence

Une version allégée tourne sur le NAS Synology pour un essai sur le réseau local : voir [deploy/nas/README.md](deploy/nas/README.md). Pour un essai sur un seul ordinateur avec Docker Desktop : [deploy/poste/README.md](deploy/poste/README.md).
