"""DAG 4 — reentrainement du modele, sous condition de repasser les controles.

CE DAG NE DEPLOIE PAS UN MODELE. IL EN FABRIQUE UN CANDIDAT.

La difference est tout le sujet. Un pipeline de reentrainement qui remplace
automatiquement le modele en production met en service, sans que personne ne
regarde, un modele dont on ignore s'il est encore equitable, encore explicable,
et encore exempt de variable interdite.

Ici, la promotion est SUSPENDUE a trois controles :

  C-1  aucune variable sensible n'a atteint le modele
       -> verifie dans entrainer.py, qui refuse de s'executer sinon

  C-5  chaque decision s'explique en moins d'une seconde, et en francais
       -> verifie dans expliquer.py, qui echoue sinon

  C-4  les ecarts d'equite restent sous les seuils du 16/08
       -> verifie dans audit.py, qui sort en erreur sinon

Un echec sur l'un des trois laisse l'ancien modele en place. Mieux vaut un
modele un peu vieux qu'un modele neuf dont personne n'a verifie la conformite.

ETAT AU 02/09/2026. Le controle C-4 est actuellement FRANCHI : M-1 vaut 0,3082
sur l'axe de l'age pour un seuil d'arret de 0,05. Ce DAG refuse donc la
promotion, et c'est le comportement attendu. La derogation du comite d'equite
(note d'equite, §8.9) autorise l'usage en demonstrateur, pas la mise en
production — et une derogation se constate a la main, elle ne s'automatise pas.

L'ORDRE DES ETAPES N'EST PAS NEGOCIABLE.

Le seuil se calcule sur des probabilites calibrees, et l'equite se mesure au
seuil reellement applique. Intervertir la calibration et le seuil produirait un
seuil faux sans qu'aucune etape n'echoue.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

DEPOT_ML = "/opt/crediscore-ml"

# Etapes de la chaine, dans l'ordre. Chacune echoue franchement si elle ne peut
# pas faire son travail : c'est ce qui permet de ne pas verifier apres coup.
ETAPES = [
    ("entrainer", "src/models/entrainer.py", "LightGBM + MLflow, controle C-1 avant le fit"),
    ("calibrer", "src/models/calibrer.py", "Regression isotonique : des probabilites justes"),
    ("seuil", "src/models/seuil.py", "Seuil et zone grise, deduits de la courbe de cout"),
    ("expliquer", "src/explain/expliquer.py", "SHAP global et local, controle C-5"),
]

parametres = {
    "owner": "equipe-ia",
    "retries": 0,
    # Zero reprise, volontairement. Un entrainement qui echoue sur un controle
    # de conformite echouera a l'identique au second essai : reessayer ne ferait
    # que retarder la lecture du journal.
    "execution_timeout": timedelta(hours=2),
}


@dag(
    dag_id="reentrainement",
    description="Reentraine, puis refuse la promotion si un controle echoue",
    # Jamais planifie. Declenche par surveillance_derive, ou a la main apres
    # une decision du comite. Un reentrainement periodique sans motif produirait
    # des modeles differents chaque semaine, sans qu'aucun ne soit meilleur.
    schedule=None,
    start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
    catchup=False,
    default_args=parametres,
    max_active_runs=1,
    tags=["crediscore", "modele", "reentrainement"],
)
def reentrainement():
    def executer(nom: str, script: str, description: str) -> BashOperator:
        """Une etape de la chaine, dans le conteneur.

        Le socle vient du data lake : c'est la sortie du DAG de construction
        des variables que l'on reentraine, pas une copie locale.
        """
        return BashOperator(
            task_id=nom,
            doc_md=f"**{description}**\n\n`{script}`",
            bash_command=(
                f'cd {DEPOT_ML} && '
                f'export CREDISCORE_SOCLE="s3://$CREDISCORE_BUCKET/curated/socle_complet" && '
                f"python {script}"
            ),
        )

    @task
    def auditer_equite() -> bool:
        """Controle C-4. Rend vrai si le modele peut etre promu.

        audit.py sort en erreur quand un seuil d'arret est franchi. On capture
        ce code au lieu de laisser la tache echouer : l'echec du controle n'est
        pas une panne du pipeline, c'est un resultat, et il doit conduire a une
        branche et non a une alerte technique.
        """
        import subprocess

        environnement = dict(os.environ)
        bucket = environnement.get("CREDISCORE_BUCKET")
        if bucket:
            environnement["CREDISCORE_SOCLE"] = f"s3://{bucket}/curated/socle_complet"

        resultat = subprocess.run(
            ["python", "src/fairness/audit.py"],
            cwd=DEPOT_ML,
            env=environnement,
            capture_output=True,
            text=True,
            check=False,
        )
        print(resultat.stdout)
        if resultat.stderr:
            print("--- sortie d'erreur ---")
            print(resultat.stderr)

        conforme = resultat.returncode == 0
        print(f"\nControle C-4 : {'PASSE' if conforme else 'ECHOUE'}")
        return conforme

    @task.branch
    def decider_promotion(conforme: bool) -> str:
        if conforme:
            return "promouvoir"
        return "garder_candidat"

    @task
    def promouvoir() -> None:
        """Le modele devient la reference : on refige le profil de derive.

        Sans cette etape, la surveillance continuerait de comparer les lots a
        la distribution de l'ANCIEN modele, et signalerait une derive
        permanente sans qu'aucune donnee n'ait change.
        """
        import subprocess

        environnement = dict(os.environ)
        bucket = environnement.get("CREDISCORE_BUCKET")
        if bucket:
            environnement["CREDISCORE_SOCLE"] = f"s3://{bucket}/curated/socle_complet"

        resultat = subprocess.run(
            ["python", "src/monitoring/derive.py", "--construire"],
            cwd=DEPOT_ML,
            env=environnement,
            capture_output=True,
            text=True,
            check=True,
        )
        print(resultat.stdout)
        print("Modele promu. Le profil de reference de la derive a ete refige.")

    @task
    def garder_candidat() -> None:
        """Le modele reste candidat. L'ancien continue de servir."""
        raise RuntimeError(
            "PROMOTION REFUSEE : le controle d'equite C-4 n'est pas passe. "
            "Le modele reentraine reste un candidat et n'est PAS mis en "
            "service ; l'ancien modele continue de repondre. "
            "Consulter docs/note_equite.md §8 pour les ecarts mesures, et le "
            "journal de la tache auditer_equite pour ceux de cette execution. "
            "Seul le comite d'equite peut lever le blocage, par une derogation "
            "ecrite et datee."
        )

    fin = EmptyOperator(task_id="fin", trigger_rule="none_failed_min_one_success")

    # La chaine s'execute en serie : chaque etape lit ce que la precedente a
    # ecrit sur disque.
    precedente = None
    for nom, script, description in ETAPES:
        etape = executer(nom, script, description)
        if precedente is not None:
            precedente >> etape
        precedente = etape

    conforme = auditer_equite()
    precedente >> conforme

    aiguillage = decider_promotion(conforme)
    aiguillage >> [promouvoir(), garder_candidat()] >> fin


reentrainement()
