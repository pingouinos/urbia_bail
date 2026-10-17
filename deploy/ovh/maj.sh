#!/bin/sh
# Mise à jour de Clefaria Loc sur l'instance, après une fusion sur main :
#   cd /srv/clefaria && ./maj.sh
# Sauvegarde d'abord la base, télécharge la nouvelle image, redémarre (les
# migrations s'appliquent au démarrage) puis vérifie que l'application répond.
set -eu
cd "$(dirname "$0")"

jour="$(date +%F-%H%M)"
echo "1/4 Sauvegarde de la base avant mise à jour…"
docker compose exec -T db pg_dump -U urbiabail --format=custom urbiabail > "sauvegardes/base-avant-maj-$jour.dump"

echo "2/4 Téléchargement de la nouvelle version…"
docker compose pull --quiet

echo "3/4 Redémarrage…"
docker compose up -d --remove-orphans
docker image prune -f > /dev/null

echo "4/4 Vérification…"
for essai in $(seq 1 30); do
  if docker compose exec -T web python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/sante/')" 2> /dev/null; then
    echo "Mise à jour terminée, l'application répond."
    exit 0
  fi
  sleep 5
done
echo "L'application ne répond pas après 2 minutes. Journal :" >&2
docker compose logs --tail 40 web >&2
echo "Base d'avant la mise à jour : sauvegardes/base-avant-maj-$jour.dump" >&2
exit 1
