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

echo "=== Installation du plugin docker compose ==="
# Le plugin n'est pas dans les dépôts Amazon Linux 2023 : on l'installe depuis
# les publications officielles Docker.
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL \
  "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

echo "=== Arborescence de travail ==="
mkdir -p /opt/crediscore/{dags,spark_jobs,monitoring,donnees}
chown -R ec2-user:ec2-user /opt/crediscore

echo "=== Verification ==="
docker --version
docker compose version

# Marqueur lu par la commande de vérification post-déploiement : tant que ce
# fichier n'existe pas, l'initialisation n'est pas terminée.
date -Iseconds > /opt/crediscore/.initialisation-terminee

echo "=== Initialisation terminee ==="
