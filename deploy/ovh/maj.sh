#!/bin/sh
# Mise à jour de Clefaria Loc sur l'instance.
#   ./maj.sh             tout de suite, après une fusion sur main
#   ./maj.sh --auto      seulement si une nouvelle image est publiée (minuteur)
#   sudo ./maj.sh --installer
#                        installe le minuteur : chaque nuit à 3 h (heure de
#                        Paris), après la sauvegarde de 2 h, --auto est lancé.
# Télécharge l'image, sauvegarde la base, redémarre (les migrations
# s'appliquent au démarrage) puis vérifie que l'application répond.
set -eu
cd "$(dirname "$0")"
dossier="$(pwd)"

if [ "${1:-}" = "--installer" ]; then
  utilisateur="${SUDO_USER:-ubuntu}"
  cat > /etc/systemd/system/clefaria-maj.service <<EOF
[Unit]
Description=Mise à jour automatique de Clefaria Loc
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$utilisateur
WorkingDirectory=$dossier
ExecStart=$dossier/maj.sh --auto
EOF
  cat > /etc/systemd/system/clefaria-maj.timer <<EOF
[Unit]
Description=Mise à jour automatique de Clefaria Loc chaque nuit

[Timer]
OnCalendar=*-*-* 03:00:00 Europe/Paris
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now clefaria-maj.timer
  echo "Mise à jour automatique installée. Prochain passage :"
  systemctl list-timers clefaria-maj.timer --no-pager | sed -n 2p
  echo "Journal des passages : journalctl -u clefaria-maj"
  exit 0
fi

# Image qui tourne (vide si l'application est arrêtée).
avant="$(docker inspect -f '{{.Image}}' "$(docker compose ps -q web)" 2> /dev/null || true)"
echo "1/4 Téléchargement de la nouvelle version…"
docker compose pull --quiet
attendue="$(docker image inspect -f '{{.Id}}' "$(docker compose config --images | grep clefaria-loc | head -1)")"
if [ "${1:-}" = "--auto" ] && [ "$avant" = "$attendue" ]; then
  echo "Déjà à jour."
  exit 0
fi

jour="$(date +%F-%H%M)"
echo "2/4 Sauvegarde de la base avant mise à jour…"
docker compose exec -T db pg_dump -U urbiabail --format=custom urbiabail > "sauvegardes/base-avant-maj-$jour.dump"

echo "3/4 Redémarrage…"
docker compose up -d --remove-orphans
docker image prune -f > /dev/null

echo "4/4 Vérification…"
for essai in $(seq 1 30); do
  if sante="$(docker compose exec -T web python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/sante/').read().decode())" 2> /dev/null)"; then
    echo "Mise à jour terminée, l'application répond : $sante"
    exit 0
  fi
  sleep 5
done
echo "L'application ne répond pas après 2 minutes. Journal :" >&2
docker compose logs --tail 40 web >&2
echo "Base d'avant la mise à jour : sauvegardes/base-avant-maj-$jour.dump" >&2
exit 1
