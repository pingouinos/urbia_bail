# Production sur OVH Public Cloud

Clefaria Loc tourne sur une instance OVH Public Cloud (Ubuntu 24.04, Docker) à l'adresse `https://app.clefaria.fr`. L'instance ne construit rien : elle télécharge l'image publiée par GitHub à chaque fusion sur `main`. Caddy, seul exposé sur Internet, obtient et renouvelle seul le certificat HTTPS ; la base PostgreSQL et Gotenberg restent internes.

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
curl -fsSLO https://raw.githubusercontent.com/pingouinos/clefaria-loc/main/deploy/ovh/maj.sh && chmod +x maj.sh
chmod 600 .env
sed -i "s/^SECRET_KEY=$/SECRET_KEY=$(openssl rand -hex 32)/; s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$(openssl rand -hex 32)/" .env
```

La dernière ligne remplit `SECRET_KEY` et `POSTGRES_PASSWORD` avec deux chaînes aléatoires ; `grep -c "=[0-9a-f]\{64\}$" .env` doit afficher `2`. Si le dépôt est privé, `curl` est refusé : copier les fichiers depuis son ordinateur avec `scp docker-compose.yml .env.example maj.sh ubuntu@<IP>:/srv/clefaria/`.

L'image est privée : se connecter une fois au registre avec un jeton GitHub (lien direct, droit `read:packages` seul : https://github.com/settings/tokens/new?scopes=read:packages&description=clefaria-prod) :

```bash
docker login ghcr.io -u pingouinos
```

### 5. Démarrage

```bash
cd /srv/clefaria
docker compose up -d
docker compose ps
```

Au bout d'une minute, `https://app.clefaria.fr/sante/` répond `{"statut": "ok"}`. Créer ensuite l'administrateur de secours :

```bash
docker compose exec web python manage.py createsuperuser
```

## Mise à jour

Après une fusion sur `main` (attendre que GitHub ait publié l'image, quelques minutes) :

```bash
cd /srv/clefaria && ./maj.sh
```

Le script sauvegarde la base (`sauvegardes/base-avant-maj-….dump`), télécharge la nouvelle image, redémarre (les migrations s'appliquent au démarrage) et vérifie que l'application répond ; sinon il affiche le journal. Les mises à jour de sécurité d'Ubuntu s'installent seules.

## Sauvegardes

Trois filets :

- le conteneur `sauvegarde` copie chaque nuit vers 2 h la base et les documents dans `/srv/clefaria/sauvegardes`, gardés 14 jours ;
- la sauvegarde automatique OVH de l'instance (rotation 7 jours) ;
- une copie hors d'OVH, à organiser : par exemple vers un stockage objet ou en rapatriant `sauvegardes/` sur un ordinateur.

Les deux premiers restent chez OVH, la troisième protège d'une erreur sur le compte OVH lui-même.

Restaurer une base :

```bash
docker compose exec -T db pg_restore -U urbiabail -d urbiabail --clean --if-exists < sauvegardes/base-AAAA-MM-JJ.dump
```
