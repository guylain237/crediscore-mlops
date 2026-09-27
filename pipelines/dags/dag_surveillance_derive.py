"""DAG 3 — surveillance de la derive, et decision de reentrainer (controle C-10).

CE QUE CE DAG SURVEILLE, ET POURQUOI PERSONNE D'AUTRE NE LE FERAIT.

Un modele ne previent jamais qu'il vieillit. Il continue de repondre, sans
erreur et sans hesitation, a une population qui n'est plus celle sur laquelle
il a appris. Les deux DAGs precedents verifient que les DONNEES arrivent bien ;
celui-ci verifie que le MODELE a encore le droit de s'en servir.

IL DISTINGUE DEUX PANNES QUI SE RESSEMBLENT.

  DERIVE DE POPULATION   les demandeurs ont change.
                         -> reentrainer sur les donnees recentes.

  DERIVE DE COUVERTURE   la SOURCE a change de perimetre : un fichier arrive
                         moins complet, un fournisseur a reduit son champ.
                         Les demandeurs, eux, sont les memes.
                         -> corriger l'alimentation. SURTOUT PAS reentrainer.

Reentrainer sur une derive de couverture graverait un defaut d'alimentation
dans le modele. Le jour ou la source redevient complete, le modele casserait a
nouveau — et cette fois sans alarme, puisqu'il aurait appris sur les donnees
incompletes.

C'est arrive des la premiere mesure du 02/09/2026 : douze variables issues de
bureau_balance semblaient deriver violemment (PSI jusqu'a 1,62) alors que le
score, lui, ne bougeait pas. La cause etait la couverture de la source, pas la
population.

CE DAG NE DEPLOIE RIEN.

Il declenche au plus un reentrainement. Le modele issu de ce reentrainement ne
remplace pas l'ancien : il devient un CANDIDAT, qui doit repasser les controles
C-1, C-4 et C-5 avant toute promotion. Voir dag_reentrainement.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.operators.empty import EmptyOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

# Le depot de la solution IA, monte par docker-compose. Le calcul de derive
# appartient au modele, pas au pipeline : il vit donc avec le modele, et
# l'orchestrateur se contente de l'appeler.
DEPOT_ML = "/opt/crediscore-ml"

# Codes de retour de src/monitoring/derive.py. Ils sont le contrat entre le
# script et ce DAG.
RIEN_A_FAIRE = 0
DERIVE_POPULATION = 1
DERIVE_COUVERTURE = 2

parametres = {
    "owner": "equipe-data",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}


@dag(
    dag_id="surveillance_derive",
    description="PSI sur les variables et sur le score, puis decision de reentrainer",
    # Hebdomadaire. Quotidien serait du bruit : une derive de population ne se
    # constitue pas en vingt-quatre heures, et une alarme qui sonne trop
    # souvent finit par ne plus etre lue.
    schedule="0 6 * * 1",
    start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
    catchup=False,
    default_args=parametres,
    max_active_runs=1,
    tags=["crediscore", "modele", "derive", "C-10"],
)
def surveillance_derive():
    @task
    def mesurer_derive() -> int:
        """Lance le controle de derive et rend son code de retour.

        On n'utilise pas BashOperator : il echouerait sur tout code non nul, et
        ici le code EST l'information. 1 et 2 ne sont pas des pannes, ce sont
        deux diagnostics differents.
        """
        import subprocess

        environnement = dict(os.environ)
        # Le socle vient du data lake, pas d'une copie locale : c'est la sortie
        # du DAG de construction des variables que l'on surveille.
        bucket = environnement.get("CREDISCORE_BUCKET")
        if bucket:
            environnement["CREDISCORE_SOCLE"] = f"s3://{bucket}/curated/socle_complet"

        resultat = subprocess.run(
            ["python", f"{DEPOT_ML}/src/monitoring/derive.py"],
            cwd=DEPOT_ML,
            env=environnement,
            capture_output=True,
            text=True,
            check=False,
        )
        # La sortie du script part dans le journal de la tache : c'est elle qui
        # porte le detail des variables, pas ce DAG.
        print(resultat.stdout)
        if resultat.stderr:
            print("--- erreurs ---")
            print(resultat.stderr)

        if resultat.returncode not in (RIEN_A_FAIRE, DERIVE_POPULATION, DERIVE_COUVERTURE):
            raise RuntimeError(
                f"Le controle de derive a echoue (code {resultat.returncode}). "
                f"Ce n'est pas un diagnostic, c'est une panne du controle."
            )
        return resultat.returncode

    @task.branch
    def orienter(code: int) -> str:
        """Aiguille selon le diagnostic, pas selon la gravite apparente.

        Une derive de couverture peut afficher un PSI dix fois plus eleve
        qu'une derive de population sans justifier le moindre reentrainement.
        """
        if code == DERIVE_POPULATION:
            return "declencher_reentrainement"
        if code == DERIVE_COUVERTURE:
            return "alerter_alimentation"
        return "aucune_action"

    @task
    def alerter_alimentation() -> None:
        """Previent l'equipe donnees : une source a change de perimetre.

        Volontairement bruyant. Une derive de couverture non traitee finit par
        devenir une derive de population apparente au reentrainement suivant,
        et la cause sera alors introuvable.
        """
        raise RuntimeError(
            "ALERTE D'ALIMENTATION : une ou plusieurs sources ont change de "
            "perimetre. Le modele reste valide et n'est PAS reentraine. "
            "Consulter le journal de la tache mesurer_derive pour la liste des "
            "variables, puis corriger l'ingestion. "
            "Reentrainer sans corriger graverait le defaut dans le modele."
        )

    declencher_reentrainement = TriggerDagRunOperator(
        task_id="declencher_reentrainement",
        trigger_dag_id="reentrainement",
        # On attend la fin : si le reentrainement echoue aux controles, cette
        # execution doit le montrer plutot que de se declarer reussie.
        wait_for_completion=True,
        poke_interval=60,
        doc_md=(
            "Declenche le reentrainement. **Le modele produit ne remplace pas "
            "l'ancien** : il doit d'abord repasser C-1, C-4 et C-5."
        ),
    )

    aucune_action = EmptyOperator(
        task_id="aucune_action",
        doc_md="Ni derive de population, ni changement de source. Le modele reste valide.",
    )

    code = mesurer_derive()
    aiguillage = orienter(code)
    aiguillage >> [declencher_reentrainement, alerter_alimentation(), aucune_action]


surveillance_derive()
