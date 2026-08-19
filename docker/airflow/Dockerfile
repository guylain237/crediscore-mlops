# Image Airflow du projet — orchestration ET exécution des jobs Spark locaux.
#
# Spark tourne en mode local dans cette image plutôt que sur un cluster séparé
# (décision D-101) : le code PySpark est strictement le même, seul le maître
# change. Passer à un cluster revient à modifier une URL de soumission.
FROM apache/airflow:2.10.5-python3.12

USER root

# Java est requis par Spark. Le JRE headless suffit et reste léger.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jre-headless curl procps \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

USER airflow

# Les contraintes officielles d'Airflow évitent qu'une dépendance transitive
# ne casse l'ordonnanceur — c'est la méthode recommandée en amont.
COPY airflow/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
