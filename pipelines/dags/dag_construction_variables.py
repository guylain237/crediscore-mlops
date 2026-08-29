"""DAG 2 — construction des variables, du fichier brut au socle complet.

Il enchaine les huit jobs Spark qui transforment 58 millions de lignes en une
ligne par dossier.

POURQUOI EN SERIE ET NON EN PARALLELE.

Six des huit jobs sont independants : on pourrait les lancer ensemble. Sur un
cluster, ce serait le bon choix. Sur cette VM — 2 vCPU, 8 Go partages avec neuf
conteneurs — ce serait le mauvais : chaque job Spark reclamerait sa memoire, ils
se la disputeraient, et le noyau finirait par en tuer un.

En serie, chaque job dispose de toute la memoire disponible. C'est plus lent sur
le papier, plus rapide en pratique, et surtout previsible. Sur l'architecture
cible, la ligne de dependances se relacherait — c'est un parametre de
dimensionnement, pas une propriete du pipeline.

IDEMPOTENCE.

Chaque job ecrit en mode overwrite sur une destination fixe. Rejouer le DAG
produit donc exactement le meme resultat, et ne peut pas creer de doublon. On
peut le relancer sans precaution particuliere.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.operators.bash import BashOperator

# Les jobs sont montes en lecture seule dans le conteneur par docker-compose.
DOSSIER_JOBS = "/opt/airflow/spark_jobs"

# Memoire allouee au driver Spark. Volontairement modeste : la VM en a 8, dont
# environ 2,5 deja pris par la pile de conteneurs. Laisser de la marge evite que
# le noyau ne tue le job au moment ou il deborde sur disque.
MEMOIRE_GO = os.environ.get("CREDISCORE_MEMOIRE_GO", "3")

# L'ordre compte pour les deux premiers : agreger_installments lit ce que
# consolider_installments a produit. Les quatre suivants sont independants entre
# eux, mais tous doivent avoir fini avant l'assemblage du socle.
JOBS = [
    ("consolider_installments", "Regroupe les versements en echeances"),
    ("agreger_installments", "Comportement de paiement par dossier"),
    ("agreger_previous", "Historique de la relation CrediScore"),
    ("agreger_pos_cash", "Tenue des credits POS et tresorerie"),
    ("agreger_bureau", "Exposition chez les autres etablissements"),
    ("agreger_bureau_balance", "Historique mensuel du bureau externe"),
    ("agreger_credit_card", "Utilisation des cartes de credit"),
]

parametres = {
    "owner": "equipe-data",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    # Un job Spark qui depasse deux heures sur ce volume est bloque, pas lent.
    "execution_timeout": timedelta(hours=2),
}


@dag(
    dag_id="construction_variables",
    description="Huit jobs Spark : de 58 M de lignes a une ligne par dossier",
    # Declenche par le DAG d'ingestion, jamais seul : il n'a pas de sens si les
    # controles de qualite des sources n'ont pas ete passes.
    schedule=None,
    start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
    catchup=False,
    default_args=parametres,
    # Un seul passage a la fois. Deux executions concurrentes ecriraient au meme
    # endroit et se marcheraient dessus.
    max_active_runs=1,
    tags=["crediscore", "bloc-3", "variables"],
)
def construction_variables():
    def lancer(nom: str, description: str) -> BashOperator:
        """Un job Spark, execute dans le conteneur Airflow.

        Le conteneur embarque deja PySpark et le connecteur S3 : c'est la meme
        image que celle utilisee en developpement, au meme commit.
        """
        return BashOperator(
            task_id=nom,
            doc_md=f"**{description}**\n\n`{nom}.py`",
            bash_command=(
                f"CREDISCORE_MEMOIRE_GO={MEMOIRE_GO} "
                f"python {DOSSIER_JOBS}/{nom}.py"
            ),
        )

    @task
    def controler_socle() -> None:
        """Controles bloquants avant que le socle ne soit considere publiable.

        Ils portent sur la SORTIE, pas sur les etapes : c'est la seule chose qui
        compte pour le modele. Un echec ici laisse le socle precedent en place —
        mieux vaut des variables d'hier que des variables fausses.
        """
        import sys

        sys.path.insert(0, DOSSIER_JOBS)
        import commun
        from pyspark.sql import functions as F

        spark = commun.creer_session("controles-socle", memoire_go=int(MEMOIRE_GO))
        socle = commun.lire(spark, "curated", "socle_complet")

        echecs = []

        # 1. Une seule ligne par dossier. Une jointure fautive dupliquerait, et
        #    le modele s'entrainerait plusieurs fois sur le meme client.
        nb = socle.count()
        nb_distincts = socle.select("SK_ID_CURR").distinct().count()
        if nb != nb_distincts:
            echecs.append(f"{nb} lignes pour {nb_distincts} dossiers distincts")

        # 2. Completude. 307 511 dossiers annotes + 48 744 a scorer.
        if nb != 356_255:
            echecs.append(f"{nb} dossiers au lieu des 356 255 attendus")

        # 3. Aucun attribut sensible. C'est le controle C-1 du plan de
        #    gouvernance, applique a la sortie du pipeline.
        sensibles = [
            c
            for c in socle.columns
            if c in ("CODE_GENDER", "DAYS_BIRTH", "NAME_FAMILY_STATUS")
        ]
        if sensibles:
            echecs.append(f"attributs sensibles presents : {sensibles}")

        # 4. Aucune valeur infinie. Une division non protegee en produirait, et
        #    elles traverseraient l'entrainement sans erreur pour ressortir en
        #    predictions aberrantes.
        ratios = [c for c in socle.columns if c.startswith("RATIO_")]
        infinis = socle.select(
            *[
                F.sum(F.when(F.isnan(c) | F.col(c).isin(float("inf"), float("-inf")), 1).otherwise(0)).alias(c)
                for c in ratios
            ]
        ).collect()[0].asDict()
        fautifs = {c: n for c, n in infinis.items() if n}
        if fautifs:
            echecs.append(f"valeurs infinies dans les ratios : {fautifs}")

        spark.stop()

        if echecs:
            raise ValueError(
                "Controles qualite en echec, le socle n'est pas publiable :\n  "
                + "\n  ".join(echecs)
            )
        print(f"Socle conforme : {nb:,} dossiers, {len(socle.columns)} colonnes.".replace(",", " "))

    precedent = None
    for nom, description in JOBS:
        courant = lancer(nom, description)
        if precedent:
            precedent >> courant
        precedent = courant

    socle = lancer("construire_socle", "Assemblage du socle et des six agregats")
    precedent >> socle >> controler_socle()


construction_variables()
