# Test sur le NAS Synology

Version allégée de l'application pour un essai sur le réseau local de l'agence, sur le DS220+ (2 Go de RAM). La production reste le VPS décrit dans le README principal.

Ce qui change par rapport au VPS : pas de HTTPS ni de Caddy (adresse `http://<IP du NAS>:8080`, accessible seulement depuis le bureau) et mémoire bornée (384 Mo pour l'application, 768 Mo pour Gotenberg, qui convertit les mandats et les baux en PDF). L'ensemble utilise environ 160 Mo au repos et 310 Mo une fois le premier PDF produit, LibreOffice restant ensuite chargé. La connexion Google ne fonctionne pas ici, car Google refuse une adresse de retour en HTTP : on teste avec un compte local et sa double authentification.

Le NAS ne construit rien et n'a pas besoin du code : il télécharge l'image de l'application, publiée par GitHub à chaque fusion sur `main` (`ghcr.io/pingouinos/urbia_bail:main`), ainsi que PostgreSQL et Gotenberg. Tout tient dans le fichier [`docker-compose.yml`](docker-compose.yml) de ce dossier, à coller dans Container Manager.

Les conteneurs utilisent directement le réseau du NAS (`network_mode: host`) plutôt que le réseau interne de Docker : sur le DS220+, ce dernier laisse ouvrir une connexion entre conteneurs mais n'y fait passer aucune donnée (l'application attend la base sans jamais l'atteindre). Seule l'application est visible depuis le bureau, sur le port 8080 ; la base (port 25432) et Gotenberg (port 23300) n'écoutent que sur le NAS lui-même.

## Mise en place dans Container Manager

Prérequis : DSM 7.2 ou plus récent et le paquet **Container Manager** installé depuis le Centre de paquets.

1. **Trouver l'IP du NAS.** *Panneau de configuration* > *Réseau* > *Interface réseau* (par exemple `192.168.1.10`).
2. **Préparer le fichier.** Sur GitHub, ouvrir `deploy/nas/docker-compose.yml`, le copier (bouton *Copy raw file*) dans un éditeur de texte et remplacer :
   - `CHANGER-CETTE-CLE` par une cinquantaine de caractères aléatoires (générateur de mots de passe) ;
   - `MOTDEPASSE`, aux deux endroits, par un même mot de passe fait uniquement de lettres et de chiffres (un `@`, `:`, `/`, `#`, `%`, `?` ou `&` casse l'adresse de connexion à la base) ;
   - `192.168.1.10`, aux deux endroits, par l'IP du NAS.
3. **Créer les dossiers.** File Station > dossier partagé `docker` (créé par Container Manager) > *Créer un dossier* `urbia`, puis, dans `urbia`, un dossier `sauvegardes` : le NAS ne le crée pas tout seul.
4. **Créer le projet.** Container Manager > *Projet* > *Créer* :
   - Nom du projet : `urbia` ;
   - Chemin : `docker/urbia` ;
   - Source : *Créer docker-compose.yml*, puis coller le texte préparé ;
   - *Suivant*, ne pas activer le portail Web Station, puis *Terminé*.

   La première fois, les téléchargements représentent environ 1 Go (quelques minutes). Le projet passe ensuite à *En cours d'exécution*, avec quatre conteneurs `urbia-web-1`, `urbia-db-1`, `urbia-gotenberg-1` et `urbia-sauvegarde-1`.
5. **Créer l'administrateur.** Container Manager > *Conteneur* > `urbia-web-1` > *Action* > *Ouvrir un terminal* > *Créer* > *Lancer avec commande* : `python manage.py createsuperuser`, puis répondre aux questions (identifiant, e-mail, mot de passe).
6. **Tester.** Depuis un poste du bureau, ouvrir `http://<IP du NAS>:8080`, se connecter avec ce compte et scanner le QR code avec une application d'authentification (Google Authenticator, Microsoft Authenticator…). L'adresse `http://<IP du NAS>:8080/sante/` doit répondre `{"statut": "ok"}` ; au premier démarrage, compter quelques secondes pour la préparation de la base.
7. **Essayer la chaîne complète.** Créer un bailleur puis un bien (ou les importer depuis Excel), créer le mandat depuis la fiche du bien et le télécharger en PDF, puis faire de même avec « Rédiger un bail ». Le premier PDF prend quelques secondes de plus, le temps que LibreOffice démarre.

Si le pare-feu de DSM est activé : *Panneau de configuration* > *Sécurité* > *Pare-feu*, autoriser le port 8080 depuis le réseau local.

## Dépannage

- **Le message parle de `pip install` ou de `Step 6/12`, ou le journal de `urbia-web-1` affiche `server closed the connection unexpectedly`** : le projet utilise un ancien fichier (construction sur le NAS, ou réseau interne de Docker). Arrêter et supprimer le projet, puis le recréer en collant le fichier actuel (étapes 2 à 4).
- **La création s'arrête sur `dependency failed to start: container urbia-db-1 is unhealthy`** : la base n'a pas démarré ; le journal de `urbia-db-1` en donne la cause (souvent `Address in use`, ci-dessous).
- **Le journal parle de `Address already in use` ou `Address in use`** : un autre service du NAS occupe déjà le port (c'est le cas du 5433 sur le DS220+). Pour l'application, remplacer `8080` par un port libre (par exemple `8090`) dans la ligne `command` et dans `CSRF_TRUSTED_ORIGINS` ; pour la base, `25432` aux trois endroits (`DATABASE_URL`, `command` et test de santé de `db`) ; pour Gotenberg, `23300` dans `GOTENBERG_URL` et dans sa `command`.
- **La page reste vide (« réponse vide », `NS_ERROR_NET_EMPTY_RESPONSE`)** : l'application n'écoute pas encore. Au premier démarrage, la préparation de la base prend jusqu'à une minute ; sinon, lire *Conteneur* > `urbia-web-1` > *Détails* > *Journal*. Un mot de passe différent entre `DATABASE_URL` et `POSTGRES_PASSWORD` fait redémarrer l'application en boucle. La base garde le mot de passe de sa création : pour en changer, supprimer le projet puis le recréer sous un autre nom, ce qui repart d'une base neuve.
- **`Bind mount failed: '/volume1/docker/urbia/sauvegardes' does not exist`** : créer le dossier `sauvegardes` dans `docker/urbia` avec File Station, puis *Action* > *Construire*. Les trois autres conteneurs tournent déjà, seul `urbia-sauvegarde-1` attend ce dossier.
- **Le téléchargement de `ghcr.io/pingouinos/urbia_bail` est refusé (`denied` ou `unauthorized`)** : sur GitHub, ouvrir le paquet `urbia_bail` du compte, puis *Package settings* > *Change visibility* > *Public*.

## Sauvegardes

Le conteneur `urbia-sauvegarde-1` copie la base (`base-AAAA-MM-JJ.dump`) et les documents (`documents-AAAA-MM-JJ.tar.gz`) une fois par jour, la première fois dix minutes après le démarrage, dans le dossier `docker/urbia/sauvegardes` visible dans File Station. Il garde les 14 derniers jours. Ces copies restent sur le NAS : pour se protéger d'une panne de disque ou d'un vol, inclure ce dossier dans une tâche Hyper Backup vers un disque USB ou un stockage en ligne.

Pour restaurer une base, depuis le terminal de `urbia-sauvegarde-1` : `pg_restore -h 127.0.0.1 -p 25432 -U urbiabail -d urbiabail --clean /sauvegardes/base-AAAA-MM-JJ.dump`.

## Ouvrir le formulaire locataire sur Internet (Cloudflare Tunnel)

Le candidat retenu complète ses informations par un lien personnel (`/locataire/…`). Pour qu'il l'ouvre depuis chez lui, Cloudflare Tunnel relie le NAS à l'adresse `https://locataire.urbia-immobilier.fr` sans ouvrir de port sur la box : le NAS se connecte à Cloudflare, qui ne lui transmet que les adresses `/locataire/` et `/static/`. L'application refuse de toute façon tout le reste sur cette adresse ; le reste de l'appli ne s'ouvre qu'au bureau. L'offre Free de Cloudflare et le tunnel sont gratuits ; le domaine reste payé chez OVH.

Le domaine doit passer chez Cloudflare (ses serveurs DNS), ce qui touche aussi la messagerie Google et le site : les étapes 1 et 2 sont à faire posément, en recopiant tous les enregistrements.

### 1. Cloudflare : ajouter le domaine

1. Créer un compte sur [dash.cloudflare.com](https://dash.cloudflare.com) avec l'adresse de l'agence.
2. *Onboard a domain* : `urbia-immobilier.fr`, offre **Free**. Cloudflare recherche les enregistrements existants, mais peut en oublier.
3. Comparer sa liste, ligne à ligne, avec la zone DNS d'OVH (*Web Cloud* > *Noms de domaine* > `urbia-immobilier.fr` > onglet *Zone DNS*) et ajouter ce qui manque. Pour la messagerie Google, il faut retrouver :
   - le ou les **MX** : `smtp.google.com` (priorité 1), ou les cinq `aspmx…` d'un compte plus ancien ;
   - le **TXT** SPF `v=spf1 include:_spf.google.com ~all` ;
   - le **TXT** `google._domainkey` (DKIM, valeur à recopier telle quelle), et `_dmarc` ou `google-site-verification` s'ils existent.

   Les enregistrements du site (`urbia-immobilier.fr`, `www`) passent en **DNS only** (nuage gris), pour que le site reste servi exactement comme aujourd'hui. MX et TXT sont toujours en DNS only.
4. Noter les deux serveurs de noms que Cloudflare indique (`….ns.cloudflare.com`).

### 2. OVH : confier le domaine à Cloudflare

1. **DNSSEC** : page *Informations générales* du domaine, cadre *Sécurité*, interrupteur « Délégation sécurisée - DNSSEC ». S'il est activé, le désactiver et attendre 24 heures avant la suite, sinon le domaine (site et e-mails) peut devenir injoignable.
2. Onglet *Serveurs DNS* > *Modifier les serveurs DNS* : remplacer les serveurs d'OVH par les deux de Cloudflare, puis *Appliquer la configuration* et *Appliquer*.
3. Attendre que Cloudflare affiche le domaine comme actif (souvent moins d'une heure, jusqu'à 48 heures). Vérifier alors qu'un e-mail envoyé depuis une adresse extérieure arrive bien et que le site s'affiche.

### 3. Cloudflare : créer le tunnel

1. [dash.cloudflare.com](https://dash.cloudflare.com) > *Networking* > *Tunnels* > *Create a tunnel* (type *Cloudflared* s'il est demandé), nom `urbia-nas`. Si Cloudflare demande d'abord de créer une organisation *Zero Trust* : choisir un nom d'équipe et l'offre **Free** ; il réclame une carte bancaire mais ne prélève rien.
2. Choisir l'environnement **Docker** et copier le **jeton** : la longue suite de caractères après `--token`, qui commence par `eyJ`. Il donne accès au tunnel : ne pas l'envoyer par e-mail ni messagerie.
3. Laisser cette page ouverte et faire l'étape 4 ; le tunnel apparaît ensuite connecté, puis *Continue*.
4. Onglet *Routes* > *Add route* > *Published application* :
   - *Subdomain* : `locataire` ; *Domain* : `urbia-immobilier.fr` ;
   - *Path* : `^/(locataire|static)/` ;
   - *Service URL* : `http://127.0.0.1:8080` (le port de l'application sur le NAS) ;
   - *Add route*.

### 4. NAS : lancer le tunnel

1. File Station > dossier `docker` > *Créer un dossier* `urbia-tunnel`.
2. Container Manager > *Projet* > *Créer* : nom `urbia-tunnel`, chemin `docker/urbia-tunnel`, *Créer docker-compose.yml*, puis coller le fichier [`tunnel/docker-compose.yml`](tunnel/docker-compose.yml) en remplaçant `JETON-DU-TUNNEL` par le jeton. *Suivant*, ne pas activer le portail Web Station, *Terminé*.

C'est un projet à part : *Arrêter* `urbia-tunnel` ferme aussitôt le formulaire depuis Internet, l'application continuant de tourner au bureau.

### 5. Application : donner l'adresse publique

Projet `urbia` > *Action* > *Arrêter*, onglet *YAML* : dans le service `web`, renseigner `URL_LOCATAIRES: "https://locataire.urbia-immobilier.fr"`, enregistrer, puis *Construire*. Les liens envoyés aux candidats utilisent alors cette adresse.

### 6. Tester

Depuis un téléphone en 4G (Wi-Fi coupé) : `https://locataire.urbia-immobilier.fr/` affiche une page introuvable (erreur 404) : c'est normal, seul le formulaire est ouvert. Sur une candidature retenue, *Envoyer le lien au locataire*, puis ouvrir le lien sur le téléphone : le formulaire s'affiche.

## Mettre à jour

*Projet* > `urbia` > *Action* > *Arrêter*, puis *Construire* : le NAS télécharge la dernière image publiée. Si le fichier `docker-compose.yml` a changé (nouveau conteneur, par exemple), coller d'abord le nouveau texte dans l'onglet *YAML* du projet, en gardant la clé, le mot de passe et l'IP déjà en place. Les migrations s'appliquent au démarrage ; les données restent dans les volumes Docker `urbia_postgres` et `urbia_documents`.

## Arrêter l'essai

*Projet* > `urbia` > *Action* > *Arrêter* libère la mémoire. *Supprimer* le projet efface aussi les données de test.
