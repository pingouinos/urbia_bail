#!/bin/sh
# Appelé par le planificateur de l'hôte, sans installer/activer de tâche ici.
# Exemple cron (utilisateur autorisé à utiliser Docker) :
# 0 6 * * * /opt/urbiabail/scripts/veille.sh >> /var/log/urbia-veille.log 2>&1
set -eu
cd "$(dirname "$0")/.."
# Évite deux collectes simultanées sur le même hôte (util-linux requis).
exec 9>"${VEILLE_VERROU:-/tmp/urbia-veille.lock}"
flock -n 9 || exit 0
docker compose exec -T web python manage.py collecter_veille
