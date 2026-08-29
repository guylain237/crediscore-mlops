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
from datetime import datetime, timedelta, timezone

import boto3
from airflow.decorators import dag, task
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

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
    tags=["crediscore", "bloc-3", "controle"],
)
def ingestion_quotidienne():
    @task
    def detecter_fichiers() -> dict:
        """Les huit fichiers attendus sont-ils presents dans raw/ ?"""
        s3 = boto3.client("s3")
        reponse = s3.list_objects_v2(Bucket=BUCKET, Prefix="raw/")
        objets = {
            o["Key"].removeprefix("raw/"): {
                "taille": o["Size"],
                "modifie": o["LastModified"].isoformat(),
            }
            for o in reponse.get("Contents", [])
        }

        manquants = [f for f in FICHIERS_ATTENDUS if f not in objets]
        if manquants:
            raise ValueError(
                f"Fichiers absents de raw/ : {manquants}. "
                f"Le traitement ne peut pas demarrer."
            )

        print(f"Les {len(FICHIERS_ATTENDUS)} fichiers attendus sont presents.")
        return {f: objets[f] for f in FICHIERS_ATTENDUS}

    @task
    def controler_fraicheur(inventaire: dict) -> None:
        """Le flux du bureau externe est-il assez recent ?"""
        modifie = datetime.fromisoformat(inventaire["bureau.csv"]["modifie"])
        age = (datetime.now(modifie.tzinfo) - modifie).days

        print(f"Flux bureau depose il y a {age} jours.")
        if age > FRAICHEUR_MAX_JOURS:
            raise ValueError(
                f"Flux bureau vieux de {age} jours, au-dela des "
                f"{FRAICHEUR_MAX_JOURS} tolerees. Un historique perime ne "
                f"reflete plus la situation du demandeur."
            )

    @task
    def controler_volumetrie(inventaire: dict) -> None:
        """Les fichiers ont-ils une taille plausible ?

        On ne compte pas les lignes ici : cela demanderait de lire 2,5 Gio pour
        un controle preliminaire. La taille en octets suffit a detecter une
        troncature ou un export partiel, qui sont les deux pannes reelles.
        """
        # Tailles de reference mesurees le 30/07/2026, en octets.
        REFERENCE = {
            "application_train.csv": 166_133_370,
            "application_test.csv": 26_567_651,
            "bureau.csv": 170_016_717,
            "bureau_balance.csv": 375_592_889,
            "previous_application.csv": 404_973_293,
            "POS_CASH_balance.csv": 392_703_158,
            "credit_card_balance.csv": 424_582_605,
            "installments_payments.csv": 723_118_349,
        }

        anomalies = []
        for fichier, attendu in REFERENCE.items():
            constate = inventaire[fichier]["taille"]
            ecart = abs(constate - attendu) / attendu
            if ecart > TOLERANCE_TAILLE:
                anomalies.append(
                    f"{fichier} : {constate:,} octets contre {attendu:,} attendus "
                    f"({ecart * 100:.1f} % d'ecart)".replace(",", " ")
                )

        if anomalies:
            raise ValueError(
                "Volumetrie anormale, publication bloquee :\n  "
                + "\n  ".join(anomalies)
            )
        print(f"Les {len(REFERENCE)} fichiers ont une taille conforme.")

    declencher_variables = TriggerDagRunOperator(
        task_id="declencher_construction_variables",
        trigger_dag_id="construction_variables",
        # Le DAG suivant ne demarre que si TOUS les controles ont reussi.
        wait_for_completion=False,
    )

    inventaire = detecter_fichiers()
    controles = [controler_fraicheur(inventaire), controler_volumetrie(inventaire)]
    controles >> declencher_variables


ingestion_quotidienne()
