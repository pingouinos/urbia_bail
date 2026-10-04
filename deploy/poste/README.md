# Essai sur un ordinateur

Pour essayer l'application sans serveur ni abonnement : elle tourne sur un ordinateur Windows, Mac ou Linux avec Docker Desktop, et s'ouvre sur `http://localhost:8080`, depuis cet ordinateur seulement. C'est un essai : la clé et le mot de passe de [`docker-compose.yml`](docker-compose.yml) sont connus de tous, on n'y met donc pas de vraies données de locataires.

1. **Installer Docker Desktop** depuis [docker.com](https://www.docker.com/products/docker-desktop/) (gratuit pour une petite entreprise), redémarrer si demandé, puis le lancer et attendre qu'il indique *Engine running*.
2. **Déposer le fichier.** Créer un dossier `urbia` (par exemple dans *Documents*) et y enregistrer [`docker-compose.yml`](docker-compose.yml) sous ce nom exact (sans `.txt` à la fin). Rien à modifier dedans.
3. **Ouvrir un terminal dans ce dossier.** Windows : clic droit dans le dossier > *Ouvrir dans le Terminal*. Mac : *Terminal*, puis `cd ~/Documents/urbia`.
4. **Démarrer** avec `docker compose up -d`. Le premier lancement télécharge environ 1 Go.
5. **Créer l'administrateur** avec `docker compose exec web python manage.py createsuperuser`, puis répondre aux questions.
6. **Tester.** Ouvrir `http://localhost:8080`, se connecter et scanner le QR code avec une application d'authentification. Le premier PDF prend jusqu'à une demi-minute, le temps que LibreOffice démarre.

`docker compose down` arrête l'essai en gardant les données ; `docker compose down -v` les efface. Pour récupérer la dernière version de l'application : `docker compose up -d` de nouveau.
