# Test sur le NAS Synology

Version allégée de l'application pour un essai sur le réseau local de l'agence, sur le DS220+ (2 Go de RAM). La production reste le VPS décrit dans le README principal.

Ce qui change par rapport au VPS : pas de HTTPS ni de Caddy (adresse `http://<IP du NAS>:8080`, accessible seulement depuis le bureau) et mémoire bornée (384 Mo pour l'application, 256 Mo pour la base, 768 Mo pour Gotenberg, qui convertit les mandats et les baux en PDF). L'ensemble utilise environ 160 Mo au repos et 310 Mo une fois le premier PDF produit, LibreOffice restant ensuite chargé. La connexion Google ne fonctionne pas ici, car Google refuse une adresse de retour en HTTP : on teste avec un compte local et sa double authentification.

## Mise en place dans Container Manager

Prérequis : DSM 7.2 ou plus récent et le paquet **Container Manager** installé depuis le Centre de paquets.

1. **Récupérer le code.** Sur GitHub, page du dépôt, bouton *Code* puis *Download ZIP*. Dans File Station, ouvrir le dossier partagé `docker` (créé par Container Manager), y déposer le ZIP, clic droit *Extraire* vers `docker/urbiabail`. Vérifier que `Dockerfile` est directement dans `docker/urbiabail` (sinon déplacer le contenu du sous-dossier `urbia_bail-…` créé par l'extraction).
2. **Trouver l'IP du NAS.** *Panneau de configuration* > *Réseau* > *Interface réseau* (par exemple `192.168.1.10`).
3. **Créer le fichier `.env`.** Sur le PC, ouvrir `deploy/nas/.env.example`, remplir :
   - `SECRET_KEY` : une cinquantaine de caractères aléatoires (générateur de mots de passe) ;
   - `POSTGRES_PASSWORD` : un mot de passe sans caractère `@`, `:` ni `/` ;
   - `ALLOWED_HOSTS` et `CSRF_TRUSTED_ORIGINS` : remplacer `192.168.1.10` par l'IP du NAS.

   L'enregistrer sous le nom `.env` (dans le Bloc-notes : *Type* = *Tous les fichiers*) et le déposer avec File Station dans `docker/urbiabail/deploy/nas`.
4. **Créer le projet.** Container Manager > *Projet* > *Créer* :
   - Nom du projet : `urbiabail` ;
   - Chemin : `docker/urbiabail/deploy/nas` ;
   - Source : *Utiliser le docker-compose.yml existant* ;
   - *Suivant*, ne pas activer le portail Web Station, puis *Terminé*.

   Le NAS ne construit rien : il télécharge l'image de l'application, publiée par GitHub à chaque mise à jour de `main`, ainsi que PostgreSQL et Gotenberg (environ 1 Go en tout la première fois, quelques minutes). Le projet passe ensuite à *En cours d'exécution*, avec trois conteneurs `urbiabail-web-1`, `urbiabail-db-1` et `urbiabail-gotenberg-1`.
5. **Créer l'administrateur.** Container Manager > *Conteneur* > `urbiabail-web-1` > *Action* > *Ouvrir un terminal* > *Créer* > *Lancer avec commande* : `python manage.py createsuperuser`, puis répondre aux questions (identifiant, e-mail, mot de passe).
6. **Tester.** Depuis un poste du bureau, ouvrir `http://<IP du NAS>:8080`, se connecter avec ce compte et scanner le QR code avec une application d'authentification (Google Authenticator, Microsoft Authenticator…). L'adresse `http://<IP du NAS>:8080/sante/` doit répondre `{"statut": "ok"}`.
7. **Essayer la chaîne complète.** Créer un bailleur puis un bien (ou les importer depuis Excel), créer le mandat depuis la fiche du bien et le télécharger en PDF, puis faire de même avec « Rédiger un bail ». Le premier PDF prend quelques secondes de plus, le temps que LibreOffice démarre.

Si le pare-feu de DSM est activé : *Panneau de configuration* > *Sécurité* > *Pare-feu*, autoriser le port 8080 depuis le réseau local.

## Dépannage

- **Le message parle de `pip install` ou de `Step 6/12`** : le dossier contient une ancienne version qui construisait l'image sur le NAS. Retélécharger le ZIP et remplacer les fichiers.
- **Le téléchargement de `ghcr.io/pingouinos/urbia_bail` est refusé (`denied` ou `unauthorized`)** : sur GitHub, ouvrir le paquet `urbia_bail` du compte, puis *Package settings* > *Change visibility* > *Public*.

## Mettre à jour

*Projet* > `urbiabail` > *Action* > *Arrêter*, puis *Construire* : le NAS télécharge la dernière image publiée. Retélécharger le ZIP (en gardant `deploy/nas/.env`) seulement si le pas-à-pas ou `docker-compose.yml` ont changé. Les migrations s'appliquent au démarrage ; les données restent dans les volumes Docker `urbiabail_postgres` et `urbiabail_documents`.

## Arrêter l'essai

*Projet* > `urbiabail` > *Action* > *Arrêter* libère la mémoire. *Supprimer* le projet efface aussi les données de test.
