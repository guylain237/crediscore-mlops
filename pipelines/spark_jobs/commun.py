"""Brique commune des jobs Spark : session, chemins, lecture et écriture.

Tous les jobs d'agrégation passent par ce module. Il porte trois
responsabilités, chacune née d'un problème rencontré :

1. **Créer une session Spark utilisable des deux côtés.** Le même code tourne
   sur le poste de développement et dans le conteneur Airflow de la VM. Seule
   la source des données change — un dossier local ici, le data lake S3 là-bas.

2. **Imposer l'interpréteur Python aux workers.** Spark lance ses processus
   Python avec le `python` du PATH, qui n'est presque jamais celui du venv. Sur
   ce poste, il s'agissait du Python 3.13 du Microsoft Store, incompatible avec
   PySpark 3.5 : les workers plantaient sur `WinError 10038`. Le module force
   donc `PYSPARK_PYTHON` sur l'interpréteur courant (décision D-112).

3. **Dimensionner pour la machine réelle.** La VM dispose de 8 Go partagés par
   neuf conteneurs. Les valeurs par défaut de Spark — 200 partitions de shuffle,
   1 Go de driver — sont calibrées pour un cluster, pas pour cela.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Doit précéder tout import de pyspark : la variable est lue au démarrage de la
# JVM, pas au moment de créer la session.
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

from pyspark.sql import DataFrame, SparkSession

# --- Zones du data lake ----------------------------------------------------
# Les mêmes noms qu'en production. En local, ils désignent des sous-dossiers ;
# sur la VM, les préfixes du bucket S3. Le code des jobs ne fait jamais la
# différence : il demande « la zone raw », pas « tel chemin ».
# Les six zones déclarées dans infra/datalake.tf. Le pipeline n'écrit que dans
# `clean` et `curated` ; `reference` lui sert à lire le dictionnaire des
# colonnes, `mlflow` appartient au serveur de suivi, et `audit` n'est
# accessible qu'au rôle de l'API — en écriture seule, pour que le journal des
# décisions ne puisse pas être altéré par celui qui l'alimente.
ZONES = ("raw", "reference", "clean", "curated", "mlflow", "audit")

# Emplacements hors S3. Surchargeables par variable d'environnement : le même
# module sert sur le poste (chemins Windows), dans le conteneur de
# développement (/donnees/...) et sur la VM (S3, voir plus bas).
_RACINE = Path(__file__).resolve().parents[3]
DOSSIER_ENTREE_LOCAL = Path(os.environ.get("CREDISCORE_ENTREE", _RACINE / "input"))
DOSSIER_TRAVAIL_LOCAL = Path(
    os.environ.get("CREDISCORE_TRAVAIL", _RACINE / "donnees_pipeline")
)


def _bucket() -> str | None:
    """Nom du bucket S3, s'il est fourni. Absent = exécution locale."""
    return os.environ.get("CREDISCORE_BUCKET")


def creer_session(nom: str, memoire_go: int = 4, coeurs: str = "*") -> SparkSession:
    """Session Spark en mode local, dimensionnée pour une seule machine.

    Le mode local est un choix d'architecture assumé (décision D-101) : le même
    code s'exécuterait sur un cluster en changeant la seule adresse du maître.
    Sur ce volume et cette machine, Spark n'apporte pas de gain de vitesse — il
    apporte la garantie de ne rien réécrire le jour où le volume décuple, et une
    dégradation propre sur disque quand la mémoire manque.
    """
    constructeur = (
        SparkSession.builder.appName(f"crediscore-{nom}")
        .master(f"local[{coeurs}]")
        .config("spark.driver.memory", f"{memoire_go}g")
        # 200 partitions par défaut sur une machine à quelques cœurs produit des
        # milliers de fichiers minuscules et un surcoût de planification qui
        # dépasse le temps de calcul.
        .config("spark.sql.shuffle.partitions", "16")
        # Laisse Spark fusionner les partitions trop petites après un shuffle.
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        # L'interface web n'a pas d'intérêt dans un job batch orchestré, et son
        # port entrerait en conflit lors d'exécutions parallèles.
        .config("spark.ui.enabled", "false")
        # Écriture Parquet : conserve les types temporels sans conversion.
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
    )

    if _bucket():
        # Sur la VM, l'accès S3 emprunte le rôle IAM de l'instance : aucune clé
        # n'est fournie ici, et c'est volontaire (contrôle C-9).
        constructeur = constructeur.config(
            "spark.hadoop.fs.s3a.aws.credentials.provider",
            "com.amazonaws.auth.DefaultAWSCredentialsProviderChain",
        )

    session = constructeur.getOrCreate()
    session.sparkContext.setLogLevel("ERROR")

    # Les workers Python de Spark sont des processus separes : ils n'heritent
    # pas du sys.path du driver. Un module appele DANS une UDF doit donc leur
    # etre expedie, sinon ils echouent sur ModuleNotFoundError alors que le
    # meme import fonctionne cote driver.
    #
    # C'est le cas de pseudonyme.py, dont agreger_installments fait une UDF
    # pour le controle C-7. Sans cette ligne, le job demarre normalement puis
    # meurt a la premiere ligne traitee.
    for module in ("pseudonyme.py",):
        fichier = Path(__file__).with_name(module)
        if fichier.exists():
            session.sparkContext.addPyFile(str(fichier))

    return session


def chemin_zone(zone: str, nom: str = "") -> str:
    """Résout une zone logique en chemin concret, local ou S3.

    `chemin_zone("curated", "instal")` donne `.../donnees_pipeline/curated/instal`
    en local, et `s3a://<bucket>/curated/instal` sur la VM.
    """
    if zone not in ZONES:
        raise ValueError(f"Zone inconnue : {zone!r}. Attendu : {ZONES}")

    bucket = _bucket()
    if bucket:
        return f"s3a://{bucket}/{zone}/{nom}" if nom else f"s3a://{bucket}/{zone}/"

    dossier = DOSSIER_ENTREE_LOCAL if zone == "raw" else DOSSIER_TRAVAIL_LOCAL / zone
    dossier.mkdir(parents=True, exist_ok=True)
    return str(dossier / nom) if nom else str(dossier)


def lire_source(session: SparkSession, fichier: str, echantillon: int = 0) -> DataFrame:
    """Lit un fichier de la zone brute.

    `echantillon` limite le nombre de lignes lues. Développer sur 100 000 lignes
    plutôt que sur 13,6 millions ramène une itération de plusieurs minutes à
    quelques secondes ; la passe complète n'est faite qu'une fois, pour les
    chiffres officiels.

    L'inférence de schéma est laissée à Spark ici, mais le typage explicite
    intervient à l'ingestion (DAG 1) : les zones `clean` et `curated` sont en
    Parquet, donc typées une fois pour toutes.
    """
    df = (
        session.read.option("header", "true")
        .option("inferSchema", "true")
        .csv(chemin_zone("raw", fichier))
    )
    return df.limit(echantillon) if echantillon else df


def ecrire(df: DataFrame, zone: str, nom: str) -> str:
    """Écrit en Parquet, en mode `overwrite`.

    Le mode `overwrite` — et non `append` — est ce qui rend le pipeline
    idempotent : rejouer un jour déjà traité produit le même résultat et ne peut
    pas créer de doublon. C'est l'exigence d'automatisation du Bloc 3 traduite en
    une ligne de code.
    """
    destination = chemin_zone(zone, nom)
    df.write.mode("overwrite").parquet(destination)
    return destination


def lire(session: SparkSession, zone: str, nom: str) -> DataFrame:
    """Relit une table produite par une étape précédente."""
    return session.read.parquet(chemin_zone(zone, nom))
