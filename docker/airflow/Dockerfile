# Image Airflow du projet — orchestration ET exécution des jobs Spark locaux.
#
# Spark tourne en mode local dans cette image plutôt que sur un cluster séparé
# (décision D-101) : le code PySpark est strictement le même, seul le maître
# change. Passer à un cluster revient à modifier une URL de soumission.
# Python 3.11, et non 3.12 : PySpark 3.5.3 déclare officiellement 3.8 à 3.11.
# Sur 3.13 les workers Python plantent (WinError 10038) ; 3.12 est hors plage
# déclarée. On aligne sur la version que le framework supporte, plutôt que de
# découvrir l'incompatibilité un soir de démonstration (décision D-112).
FROM apache/airflow:2.10.5-python3.11

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

# CONNECTEUR S3.
#
# PySpark n'embarque PAS de quoi lire s3a://. Sans ces deux bibliothèques, tout
# job pointant vers le data lake échoue sur un ClassNotFoundException — et
# l'échec ne survient qu'à l'exécution, jamais à l'écriture du code.
#
# Les versions ne sont pas au choix : hadoop-aws doit correspondre EXACTEMENT au
# Hadoop embarqué par PySpark (vérifié dans l'image : hadoop-client 3.3.4), et le
# SDK AWS à celui dont cette version de hadoop-aws dépend. Un écart d'une version
# mineure suffit à produire des erreurs de méthode introuvable.
#
# Les jars sont intégrés à l'image plutôt que téléchargés au lancement par
# spark.jars.packages : un job de production ne doit pas dépendre d'un dépôt Maven
# joignable, et la version doit être figée comme le reste.
ARG VERSION_HADOOP=3.3.4
ARG VERSION_SDK_AWS=1.12.262

ENV MAVEN=https://repo1.maven.org/maven2
ENV JARS=/home/airflow/.local/lib/python3.11/site-packages/pyspark/jars

RUN curl -fsSL -o "${JARS}/hadoop-aws-${VERSION_HADOOP}.jar" \
      "${MAVEN}/org/apache/hadoop/hadoop-aws/${VERSION_HADOOP}/hadoop-aws-${VERSION_HADOOP}.jar" \
 && curl -fsSL -o "${JARS}/aws-java-sdk-bundle-${VERSION_SDK_AWS}.jar" \
      "${MAVEN}/com/amazonaws/aws-java-sdk-bundle/${VERSION_SDK_AWS}/aws-java-sdk-bundle-${VERSION_SDK_AWS}.jar" \
 && ls -1 "${JARS}" | grep -E "hadoop-aws|aws-java-sdk"
