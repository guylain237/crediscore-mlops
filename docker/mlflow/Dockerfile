# Serveur MLflow : suivi des expériences et registre des modèles.
# Métadonnées dans PostgreSQL, artefacts dans le data lake S3 chiffré —
# aucun identifiant AWS n'est fourni : le rôle IAM de l'instance suffit.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
      mlflow==2.17.2 \
      psycopg2-binary>=2.9 \
      boto3>=1.35

EXPOSE 5000
