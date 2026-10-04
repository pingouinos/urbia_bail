# Test sur le NAS Synology

Version allégée de l'application pour un essai sur le réseau local de l'agence, sur le DS220+ (2 Go de RAM). La production reste le VPS décrit dans le README principal.

Ce qui change par rapport au VPS : pas de HTTPS ni de Caddy (adresse `http://<IP du NAS>:8080`, accessible seulement depuis le bureau) et mémoire bornée (384 Mo pour l'application, 768 Mo pour Gotenberg, qui convertit les mandats et les baux en PDF). L'ensemble utilise environ 160 Mo au repos et 310 Mo une fois le premier PDF produit, LibreOffice restant ensuite chargé. La connexion Google ne fonctionne pas ici, car Google refuse une adresse de retour en HTTP : on teste avec un compte local et sa double authentification.

Le NAS ne construit rien et n'a pas besoin du code : il télécharge l'image de l'application, publiée par GitHub à chaque fusion sur `main` (`ghcr.io/pingouinos/urbia_bail:main`), ainsi que PostgreSQL et Gotenberg. Tout tient dans le fichier [`docker-compose.yml`](docker-compose.yml) de ce dossier, à coller dans Container Manager.

Les conteneurs utilisent directement le réseau du NAS (`network_mode: host`) plutôt que le réseau interne de Docker : sur le DS220+, ce dernier laisse ouvrir une connexion entre conteneurs mais n'y fait passer aucune donnée (l'application attend la base sans jamais l'atteindre). Seule l'application est visible depuis le bureau, sur le port 8080 ; la base (port 5433) et Gotenberg (port 3300) n'écoutent que sur le NAS lui-même.

## Mise en place dans Container Manager

Prérequis : DSM 7.2 ou plus récent et le paquet **Container Manager** installé depuis le Centre de paquets.

1. **Trouver l'IP du NAS.** *Panneau de configuration* > *Réseau* > *Interface réseau* (par exemple `192.168.1.10`).
2. **Préparer le fichier.** Sur GitHub, ouvrir `deploy/nas/docker-compose.yml`, le copier (bouton *Copy raw file*) dans un éditeur de texte et remplacer :
   - `CHANGER-CETTE-CLE` par une cinquantaine de caractères aléatoires (générateur de mots de passe) ;
   - `MOTDEPASSE`, aux deux endroits, par un même mot de passe fait uniquement de lettres et de chiffres (un `@`, `:`, `/`, `#`, `%`, `?` ou `&` casse l'adresse de connexion à la base) ;
   - `192.168.1.10`, aux deux endroits, par l'IP du NAS.
3. **Créer un dossier vide.** File Station > dossier partagé `docker` (créé par Container Manager) > *Créer un dossier* `urbia`.
4. **Créer le projet.** Container Manager > *Projet* > *Créer* :
   - Nom du projet : `urbia` ;
   - Chemin : `docker/urbia` ;
   - Source : *Créer docker-compose.yml*, puis coller le texte préparé ;
   - *Suivant*, ne pas activer le portail Web Station, puis *Terminé*.

   La première fois, les téléchargements représentent environ 1 Go (quelques minutes). Le projet passe ensuite à *En cours d'exécution*, avec trois conteneurs `urbia-web-1`, `urbia-db-1` et `urbia-gotenberg-1`.
5. **Créer l'administrateur.** Container Manager > *Conteneur* > `urbia-web-1` > *Action* > *Ouvrir un terminal* > *Créer* > *Lancer avec commande* : `python manage.py createsuperuser`, puis répondre aux questions (identifiant, e-mail, mot de passe).
6. **Tester.** Depuis un poste du bureau, ouvrir `http://<IP du NAS>:8080`, se connecter avec ce compte et scanner le QR code avec une application d'authentification (Google Authenticator, Microsoft Authenticator…). L'adresse `http://<IP du NAS>:8080/sante/` doit répondre `{"statut": "ok"}` ; au premier démarrage, compter quelques secondes pour la préparation de la base.
7. **Essayer la chaîne complète.** Créer un bailleur puis un bien (ou les importer depuis Excel), créer le mandat depuis la fiche du bien et le télécharger en PDF, puis faire de même avec « Rédiger un bail ». Le premier PDF prend quelques secondes de plus, le temps que LibreOffice démarre.

Si le pare-feu de DSM est activé : *Panneau de configuration* > *Sécurité* > *Pare-feu*, autoriser le port 8080 depuis le réseau local.

## Dépannage

- **Le message parle de `pip install` ou de `Step 6/12`, ou le journal de `urbia-web-1` affiche `server closed the connection unexpectedly`** : le projet utilise un ancien fichier (construction sur le NAS, ou réseau interne de Docker). Arrêter et supprimer le projet, puis le recréer en collant le fichier actuel (étapes 2 à 4).
- **Le journal parle de `Address already in use`** : un autre service du NAS occupe déjà le port. Pour l'application, remplacer `8080` par un port libre (par exemple `8090`) dans la ligne `command` et dans `CSRF_TRUSTED_ORIGINS` ; pour la base, `5433` aux trois endroits (`DATABASE_URL`, `command` et test de santé de `db`) ; pour Gotenberg, `3300` dans `GOTENBERG_URL` et dans sa `command`.
- **La page reste vide (« réponse vide », `NS_ERROR_NET_EMPTY_RESPONSE`)** : l'application n'écoute pas encore. Au premier démarrage, la préparation de la base prend jusqu'à une minute ; sinon, lire *Conteneur* > `urbia-web-1` > *Détails* > *Journal*. Un mot de passe différent entre `DATABASE_URL` et `POSTGRES_PASSWORD` fait redémarrer l'application en boucle. La base garde le mot de passe de sa création : pour en changer, supprimer le projet puis le recréer sous un autre nom, ce qui repart d'une base neuve.
- **Le téléchargement de `ghcr.io/pingouinos/urbia_bail` est refusé (`denied` ou `unauthorized`)** : sur GitHub, ouvrir le paquet `urbia_bail` du compte, puis *Package settings* > *Change visibility* > *Public*.

## Mettre à jour

*Projet* > `urbia` > *Action* > *Arrêter*, puis *Construire* : le NAS télécharge la dernière image publiée. Les migrations s'appliquent au démarrage ; les données restent dans les volumes Docker `urbia_postgres` et `urbia_documents`.

## Arrêter l'essai

*Projet* > `urbia` > *Action* > *Arrêter* libère la mémoire. *Supprimer* le projet efface aussi les données de test.
