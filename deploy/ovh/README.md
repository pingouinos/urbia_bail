# Production sur OVH Public Cloud

Clefaria Loc tourne sur une instance OVH Public Cloud (Ubuntu 24.04, Docker) à l'adresse `https://app.clefaria.fr`. L'instance ne construit rien : elle télécharge l'image publiée par GitHub à chaque fusion sur `main`, comme le NAS. Caddy, seul exposé sur Internet, obtient et renouvelle seul le certificat HTTPS ; la base PostgreSQL et Gotenberg restent internes.

Tout tient dans deux fichiers posés dans `/srv/clefaria` : [`docker-compose.yml`](docker-compose.yml) et `.env` (copie remplie de [`.env.example`](.env.example)). Les données y vivent aussi, sur un volume Block Storage : la base dans `postgres/`, les documents dans `documents/`, les copies quotidiennes dans `sauvegardes/`. Supprimer, recréer ou agrandir l'instance n'y touche pas.

## Installation

Les commandes se tapent sur l'instance, connecté avec `ssh ubuntu@<IP>`.

### 1. Docker

```bash
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
```

Se déconnecter (`exit`), se reconnecter, puis vérifier : `docker run --rm hello-world`.

### 2. Volume des données

Dans l'espace OVH : *Public Cloud* > *Block Storage* > *Créer un volume*, 20 Go, même région que l'instance, puis l'attacher à l'instance. Sur l'instance, `lsblk` le montre comme un disque de 20 Go sans partition, en général `sdb`. Le formater et le monter (à faire **une seule fois** : formater efface le volume) :

```bash
sudo mkfs.ext4 /dev/sdb
sudo mkdir -p /srv/clefaria
echo "UUID=$(sudo blkid -s UUID -o value /dev/sdb) /srv/clefaria ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount -a
sudo mkdir /srv/clefaria/documents /srv/clefaria/sauvegardes
sudo chown -R ubuntu: /srv/clefaria
```

`df -h /srv/clefaria` doit afficher environ 20 Go. Le dossier `documents` appartient à `ubuntu`, dont le numéro (1000) est aussi celui de l'application dans son conteneur.

### 3. Nom de domaine

Dans l'espace OVH : *Web Cloud* > *Noms de domaine* > `clefaria.fr` > *Zone DNS* > *Ajouter une entrée* > **A**, sous-domaine `app`, cible l'IP de l'instance. Vérifier depuis l'instance avec `getent hosts app.clefaria.fr` (quelques minutes à quelques heures). Caddy ne peut obtenir le certificat qu'une fois cette ligne visible.

### 4. Fichiers et réglages

```bash
cd /srv/clefaria
curl -fsSLO https://raw.githubusercontent.com/pingouinos/clefaria-loc/main/deploy/ovh/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/pingouinos/clefaria-loc/main/deploy/ovh/.env.example -o .env
chmod 600 .env
openssl rand -hex 32; openssl rand -hex 32
nano .env
```

Dans `.env`, coller les deux chaînes aléatoires dans `SECRET_KEY` et `POSTGRES_PASSWORD`, vérifier `DOMAINE`, puis enregistrer (Ctrl+O, Entrée, Ctrl+X). Si le dépôt est privé, `curl` est refusé : copier les deux fichiers depuis son ordinateur avec `scp docker-compose.yml .env.example ubuntu@<IP>:/srv/clefaria/`.

L'image est privée : se connecter une fois au registre avec un jeton GitHub (*Settings* > *Developer settings* > *Personal access tokens (classic)*, droit `read:packages` seul) :

```bash
docker login ghcr.io -u pingouinos
```

### 5. Démarrage

```bash
cd /srv/clefaria
docker compose up -d
docker compose ps
```

Au bout d'une minute, `https://app.clefaria.fr/sante/` répond `{"statut": "ok"}`. Créer ensuite l'administrateur de secours (sauf si l'on reprend la base du NAS, étape suivante) :

```bash
docker compose exec web python manage.py createsuperuser
```

## Reprise des données du NAS

À faire hors des heures de travail de l'agence : ce qui serait saisi sur le NAS après la copie serait perdu.

1. Sur le NAS, prendre les copies du jour dans `docker/urbia/sauvegardes` (File Station) : `base-AAAA-MM-JJ.dump` et `documents-AAAA-MM-JJ.tar.gz`. Pour en faire une toute fraîche, redémarrer le conteneur `urbia-sauvegarde-1` et attendre dix minutes. Puis arrêter le projet `urbia` dans Container Manager.
2. Les copier vers l'instance depuis son ordinateur : `scp base-*.dump documents-*.tar.gz ubuntu@<IP>:/srv/clefaria/sauvegardes/`.
3. Sur l'instance :

```bash
cd /srv/clefaria
docker compose stop web purge
docker compose exec -T db pg_restore -U urbiabail -d urbiabail --clean --if-exists --no-owner < sauvegardes/base-AAAA-MM-JJ.dump
tar xzf sauvegardes/documents-AAAA-MM-JJ.tar.gz -C documents
docker compose start web purge
```

4. Se connecter sur `https://app.clefaria.fr` avec son compte habituel et ouvrir un bail et un mandat pour vérifier les documents. Le NAS reste arrêté : il ne sert plus que de copie de secours.

## Mise à jour

Après une fusion sur `main` (et une sauvegarde avant une migration de base) :

```bash
cd /srv/clefaria
docker compose pull && docker compose up -d
```

Les migrations s'appliquent au démarrage de l'application. `docker compose logs -f web` suit son journal.

## Sauvegardes

Trois filets :

- le conteneur `sauvegarde` copie chaque nuit vers 2 h la base et les documents dans `/srv/clefaria/sauvegardes`, gardés 14 jours ;
- la sauvegarde automatique OVH de l'instance (rotation 7 jours) ;
- une copie hors d'OVH, à organiser : par exemple rapatrier `sauvegardes/` sur le NAS de l'agence, ou vers un stockage objet.

Les deux premiers restent chez OVH, la troisième protège d'une erreur sur le compte OVH lui-même.

Restaurer une base :

```bash
docker compose exec -T db pg_restore -U urbiabail -d urbiabail --clean --if-exists < sauvegardes/base-AAAA-MM-JJ.dump
```
