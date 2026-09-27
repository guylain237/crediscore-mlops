"""DAG 1 — controle du data lake avant tout traitement.

Ce DAG ne transforme rien. Il verifie que les donnees sont la, completes et
fraiches, et il BLOQUE la suite si ce n'est pas le cas.

C'est volontaire : mieux vaut ne pas produire de variables du tout que d'en
produire a partir d'un fichier tronque. Un modele nourri de donnees corrompues
rend des decisions corrompues, et rien ne le signale.

Il tourne tous les jours a 2 h. En cas de succes, il declenche le DAG des
variables.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from airflow.decorators import dag, task
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

# Les controles qualite vivent hors des DAGs pour pouvoir etre testes sans S3
# ni Airflow.
#
# Deux emplacements possibles, et il faut les accepter tous les deux : dans le
# conteneur, docker-compose monte le dossier a cote des DAGs ; en integration
# continue et sur le poste, le depot est simplement clone. Coder un seul chemin
# en dur ferait echouer l'analyse du DAG dans l'autre contexte — et une
# analyse qui echoue ne se voit pas, le DAG disparait de l'interface.
for _chemin in ("/opt/airflow/pipelines", str(Path(__file__).resolve().parents[1])):
    if _chemin not in sys.path:
        sys.path.insert(0, _chemin)
from qualite import controles as qualite

BUCKET = os.environ.get("CREDISCORE_BUCKET", "")

# Les huit fichiers attendus, avec le nombre de lignes mesure le 28/08/2026.
# La zone raw/ contient aussi le dictionnaire des colonnes : on verifie donc la
# presence de ces huit-la nommement, plutot que de compter les objets presents.
FICHIERS_ATTENDUS = {
    "application_train.csv": 307_511,
    "application_test.csv": 48_744,
    "bureau.csv": 1_716_428,
    "bureau_balance.csv": 27_299_925,
    "previous_application.csv": 1_670_214,
    "POS_CASH_balance.csv": 10_001_358,
    "credit_card_balance.csv": 3_840_312,
    "installments_payments.csv": 13_605_401,
}

# Tolerance sur la taille d'un fichier. Un flux quotidien varie legerement d'un
# jour a l'autre ; une variation de plus de 20 % signale autre chose qu'une
# fluctuation normale — une troncature, un export partiel, un changement de
# format en amont.
TOLERANCE_TAILLE = 0.20

# Fraicheur maximale du flux bureau.
#
# En production, ce seuil vaut 48 heures : un historique de credit vieux de
# trois jours ne reflete plus la situation du demandeur. Sur le demonstrateur,
# le jeu de donnees est fige depuis le 30/07, d'ou une valeur large. Le controle
# est le meme, seul le seuil change — et il est ici pour etre exerce, pas pour
# faire joli.
FRAICHEUR_MAX_JOURS = int(os.environ.get("CREDISCORE_FRAICHEUR_JOURS", "365"))

parametres = {
    "owner": "equipe-data",
    # Trois tentatives avant d'abandonner. Un appel S3 qui expire se resout
    # souvent tout seul ; seuls les echecs reels doivent remonter.
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
}


@dag(
    dag_id="ingestion_quotidienne",
    description="Controle la presence, la volumetrie et la fraicheur des sources",
    schedule="0 2 * * *",
    start_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
    catchup=False,
    default_args=parametres,
    tags=["crediscore", "pipeline", "controle"],
)
def ingestion_quotidienne():
    @task
    def detecter_fichiers() -> dict:
        """Inventorie raw/ et verifie que les huit fichiers attendus sont la."""
        s3 = boto3.client("s3")
        reponse = s3.list_objects_v2(Bucket=BUCKET, Prefix="raw/")
        objets = {
            o["Key"].removeprefix("raw/"): {
                "taille": o["Size"],
                "modifie": o["LastModified"].isoformat(),
            }
            for o in reponse.get("Contents", [])
        }

        # Le controle lui-meme vit dans pipelines/qualite : il est ainsi
        # exerce par des tests, sans avoir besoin de S3 ni d'Airflow.
        print(qualite.controler_presence(objets, list(FICHIERS_ATTENDUS)))
        return {f: objets[f] for f in FICHIERS_ATTENDUS}

    @task
    def controler_volumetrie(inventaire: dict) -> None:
        """Controle C-6, volet volumetrie."""
        print(qualite.controler_volumetrie(inventaire))

    @task
    def controler_fraicheur(inventaire: dict) -> None:
        """Controle C-6, volet fraicheur."""
        print(qualite.controler_fraicheur(
            inventaire, maximum_jours=FRAICHEUR_MAX_JOURS
        ))

    declencher_variables = TriggerDagRunOperator(
        task_id="declencher_construction_variables",
        trigger_dag_id="construction_variables",
        # Le DAG suivant ne demarre que si TOUS les controles ont reussi.
        wait_for_completion=False,
    )

    inventaire = detecter_fichiers()
    # Nom volontairement different du module qualite : une variable locale
    # appelee 'controles' masquerait l'import dans les taches ci-dessus, et
    # l'erreur ne surviendrait qu'a l'execution du DAG.
    etapes = [controler_volumetrie(inventaire), controler_fraicheur(inventaire)]
    etapes >> declencher_variables


ingestion_quotidienne()
