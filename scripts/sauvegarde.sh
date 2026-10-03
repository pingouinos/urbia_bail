#!/bin/sh
# Sauvegarde quotidienne de la base et des documents, à lancer par cron sur le VPS :
#   0 2 * * * /opt/urbiabail/scripts/sauvegarde.sh
# Les archives restent 14 jours dans SAUVEGARDES ; à recopier hors du VPS
# (NAS de l'agence, stockage objet OVH).
set -eu
cd "$(dirname "$0")/.."
SAUVEGARDES="${SAUVEGARDES:-/var/backups/urbiabail}"
JOUR="$(date +%F)"
mkdir -p "$SAUVEGARDES"
docker compose exec -T db pg_dump -U urbiabail --format=custom urbiabail > "$SAUVEGARDES/base-$JOUR.dump"
docker compose run --rm --no-deps -T --user root -v "$SAUVEGARDES:/sauvegardes" --entrypoint tar web \
  czf "/sauvegardes/documents-$JOUR.tar.gz" -C /documents .
find "$SAUVEGARDES" -type f -mtime +14 -delete
