#!/usr/bin/env bash
# Met à jour la VM de traitement à partir du dépôt, et relance ce qu'il faut.
#
# À exécuter SUR LA VM :
#   /opt/crediscore/depot/scripts/mise-a-jour.sh
#
# Ou depuis le poste, en une commande :
#   ssh ec2-user@$(terraform output -raw ip_publique_vm) \
#       /opt/crediscore/depot/scripts/mise-a-jour.sh
#
# La VM tire le code par une clé de déploiement en lecture seule (D-109) : elle
# peut lire le dépôt, jamais y écrire. Ce qui tourne correspond donc toujours à
# un commit identifiable — le SHA affiché ci-dessous est celui qu'on montre
# lors de la démonstration.

set -euo pipefail

DEPOT=/opt/crediscore/depot
cd "$DEPOT"

echo "=== Récupération du code ==="
avant=$(git rev-parse --short HEAD)
git pull --ff-only
apres=$(git rev-parse --short HEAD)

if [ "$avant" = "$apres" ]; then
  echo "Déjà à jour sur $apres — rien à relancer."
  exit 0
fi

echo
echo "Passage de $avant à $apres :"
git log --oneline "$avant..$apres" | sed 's/^/  /'

# Les DAGs et les jobs Spark sont montés en lecture seule dans les conteneurs :
# Airflow les relit tout seul, aucun redémarrage n'est nécessaire pour eux.
# En revanche, une modification du docker-compose, d'un Dockerfile ou de la
# configuration de supervision demande une reconstruction.
modifies=$(git diff --name-only "$avant..$apres")

if echo "$modifies" | grep -qE '^docker/'; then
  echo
  echo "=== docker/ modifié : reconstruction et redémarrage ==="
  docker compose -f docker/docker-compose.yml up -d --build
elif echo "$modifies" | grep -qE '^monitoring/'; then
  echo
  echo "=== monitoring/ modifié : redémarrage de la supervision ==="
  docker compose -f docker/docker-compose.yml restart prometheus grafana
else
  echo
  echo "=== Seuls les pipelines ont changé : Airflow les relit tout seul ==="
fi

echo
echo "=== État de la pile ==="
docker compose -f docker/docker-compose.yml ps --format "table {{.Service}}\t{{.Status}}"
echo
echo "Commit déployé : $apres"
