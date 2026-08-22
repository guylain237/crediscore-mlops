#!/bin/bash
# Initialisation de la VM de traitement — exécuté UNE SEULE FOIS au premier
# démarrage, par le mécanisme user_data d'EC2, en tant que root.
#
# Objectif : la machine doit être prête à recevoir la pile docker-compose du
# 18/08 sans aucune installation manuelle. Une VM qu'on prépare à la main n'est
# pas reproductible — et le jour où elle est détruite, tout est à refaire.
#
# Journal d'exécution consultable sur la VM :
#   sudo cat /var/log/cloud-init-output.log

set -euo pipefail

echo "=== Mise a jour du systeme ==="
dnf update -y

echo "=== Installation des outils de base ==="
dnf install -y docker git jq unzip

echo "=== Demarrage de Docker ==="
systemctl enable --now docker

# Permet à l'utilisateur ec2-user de lancer docker sans sudo.
# Prend effet à la prochaine ouverture de session SSH.
usermod -aG docker ec2-user

# Versions FIGÉES, et non « latest » : une image reconstruite dans trois mois
# doit se comporter exactement comme celle qui a été filmée pour le jury.
# C'est aussi ce qui a mordu le 19/08 — « latest » avait livré un Compose v5.5
# qui exige buildx >= 0.17, absent de la machine : la construction échouait.
VERSION_COMPOSE=v2.31.0
VERSION_BUILDX=v0.19.3

echo "=== Installation des plugins docker compose et buildx ==="
mkdir -p /usr/local/lib/docker/cli-plugins

curl -SL   "https://github.com/docker/compose/releases/download/${VERSION_COMPOSE}/docker-compose-linux-x86_64"   -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# buildx est requis par `docker compose build` depuis Compose v2.
curl -SL   "https://github.com/docker/buildx/releases/download/${VERSION_BUILDX}/buildx-${VERSION_BUILDX}.linux-amd64"   -o /usr/local/lib/docker/cli-plugins/docker-buildx
chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx

echo "=== Arborescence de travail ==="
mkdir -p /opt/crediscore/{dags,spark_jobs,monitoring,donnees}
chown -R ec2-user:ec2-user /opt/crediscore

echo "=== Verification ==="
docker --version
docker compose version
docker buildx version

# Marqueur lu par la commande de vérification post-déploiement : tant que ce
# fichier n'existe pas, l'initialisation n'est pas terminée.
date -Iseconds > /opt/crediscore/.initialisation-terminee

echo "=== Initialisation terminee ==="
