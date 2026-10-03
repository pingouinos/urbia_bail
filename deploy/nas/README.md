# Test sur le NAS Synology

Version allégée de l'application pour un essai sur le réseau local de l'agence, sur le DS220+ (2 Go de RAM). La production reste le VPS décrit dans le README principal.

Ce qui change par rapport au VPS : pas de HTTPS ni de Caddy (adresse `http://<IP du NAS>:8080`, accessible seulement depuis le bureau) et mémoire bornée (384 Mo pour l'application, 256 Mo pour la base, 768 Mo pour Gotenberg, qui convertit les mandats et les baux en PDF). L'ensemble utilise environ 160 Mo au repos et 310 Mo une fois le premier PDF produit, LibreOffice restant ensuite chargé. La connexion Google ne fonctionne pas ici, car Google refuse une adresse de retour en HTTP : on teste avec un compte local et sa double authentification.

## Mise en place dans Container Manager

Prérequis : DSM 7.2 ou plus récent et le paquet **Container Manager** installé depuis le Centre de paquets.

1. **Récupérer le code.** Sur GitHub, page du dépôt, bouton *Code* puis *Download ZIP*. Dans File Station, ouvrir le dossier partagé `docker` (créé par Container Manager), y déposer le ZIP, clic droit *Extraire* vers `docker/urbiabail`. Vérifier que `Dockerfile` est directement dans `docker/urbiabail` (sinon déplacer le contenu du sous-dossier `urbia_bail-main`).
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

   La première fois, la construction de l'image et le téléchargement de Gotenberg (environ 1 Go) prennent une dizaine de minutes. Le projet passe ensuite à *En cours d'exécution*, avec trois conteneurs `urbiabail-web-1`, `urbiabail-db-1` et `urbiabail-gotenberg-1`.
5. **Créer l'administrateur.** Container Manager > *Conteneur* > `urbiabail-web-1` > *Action* > *Ouvrir un terminal* > *Créer* > *Lancer avec commande* : `python manage.py createsuperuser`, puis répondre aux questions (identifiant, e-mail, mot de passe).
6. **Tester.** Depuis un poste du bureau, ouvrir `http://<IP du NAS>:8080`, se connecter avec ce compte et scanner le QR code avec une application d'authentification (Google Authenticator, Microsoft Authenticator…). L'adresse `http://<IP du NAS>:8080/sante/` doit répondre `{"statut": "ok"}`.
7. **Essayer la chaîne complète.** Créer un bailleur puis un bien (ou les importer depuis Excel), créer le mandat depuis la fiche du bien et le télécharger en PDF, puis faire de même avec « Rédiger un bail ». Le premier PDF prend quelques secondes de plus, le temps que LibreOffice démarre.

Si le pare-feu de DSM est activé : *Panneau de configuration* > *Sécurité* > *Pare-feu*, autoriser le port 8080 depuis le réseau local.

## Dépannage

- **La construction échoue sur `pip install` (« Could not find a version that satisfies the requirement Django »)** : la construction n'atteint pas Internet. Le projet construit déjà l'image sur le réseau du NAS (`network: host`) ; si l'erreur persiste, vérifier dans *Panneau de configuration* > *Réseau* > *Général* que la passerelle et le serveur DNS sont renseignés (par exemple l'adresse de la box, ou `1.1.1.1`), puis relancer *Construire*.

## Mettre à jour

Télécharger le nouveau ZIP et remplacer les fichiers de `docker/urbiabail` (garder `deploy/nas/.env`). Puis *Projet* > `urbiabail` > *Action* > *Arrêter*, *Construire*, et *Démarrer*. Les migrations s'appliquent au démarrage ; les données restent dans les volumes Docker `urbiabail_postgres` et `urbiabail_documents`.

## Arrêter l'essai

*Projet* > `urbiabail` > *Action* > *Arrêter* libère la mémoire. *Supprimer* le projet efface aussi les données de test.
