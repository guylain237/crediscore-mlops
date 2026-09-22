# CrediScore — MLOps : infrastructure, pipelines et CI/CD

**Projet de certification — Architecte en IA (Mastère 2)**
**Auteur :** Tagne Guylain Florian

Ce dépôt est le **dépôt n°2** exigé par le Bloc 4 (déploiement CI/CD), et héberge
aussi les livrables code des **Blocs 2** (infrastructure as code) et **3**
(pipelines de données).

> Le dépôt n°1 — développement de la solution IA — est ici :
> [crediscore-ml](https://github.com/guylain237/crediscore-ml)

## Architecture

Des 8 fichiers sources (3 systèmes : souscription, cœur crédit, bureau de crédit
externe) jusqu'au scoring en production :

```
Sources CSV ──► Ingestion (Airflow) ──► Data lake S3 (raw / clean / curated)
                                              │
                                    Traitement PySpark
                                  (jointures + agrégations)
                                              │
                              Feature store PostgreSQL ◄──── identique
                                     │                 entraînement / scoring
                     ┌───────────────┴───────────────┐
              Entraînement                    API de scoring (FastAPI)
           (LightGBM + MLflow)              score + SHAP + motifs de refus
                     │                               │
                CI/CD GitHub Actions          Kubernetes + HPA
              (tests, build, deploy)      Prometheus / Grafana + alertes
                     └────── détection de dérive → réentraînement ──────┘
```

## Structure du dépôt

```
├── infra/              # Bloc 2 — Terraform : réseau, S3, VM, IAM, chiffrement
├── pipelines/          # Bloc 3 — DAGs Airflow + jobs PySpark
│   ├── dags/
│   └── spark_jobs/
├── api/                # API de scoring FastAPI (score + SHAP + audit log)
├── docker/             # Dockerfiles (api, airflow, entraînement)
├── k8s/                # Manifests : deployment, service, HPA
├── monitoring/         # Prometheus + dashboards Grafana exportés
├── .github/workflows/  # CI/CD : lint, tests, build, déploiement, réentraînement
└── docs/               # Diagrammes d'architecture et de pipeline, journal des décisions
```

## Démonstrateur vs cible

Le code IaC est paramétré par variables : le **démonstrateur** (petite VM, Spark
local, k3s) et la **cible** (cluster Spark 3 nœuds, PostgreSQL managé, K8s
multi-pods) partagent les mêmes briques logicielles et le même code. Le
dimensionnement est une variable d'entrée, pas une réécriture — voir
`docs/decisions.md`.

## Installation (poste de développement)

Environnement virtuel dédié obligatoire — jamais le Python global ni Anaconda
(voir décision D-103 dans [`docs/decisions.md`](docs/decisions.md)) :

```powershell
python -m venv .venv
.venv\Scripts\activate                  # le prompt doit afficher (.venv)
python -m pip install -r requirements.txt
```

Vérification (le test échoue si Anaconda ou le Python global est actif) :

```powershell
pytest tests\test_environment.py -q
```

Airflow et Spark ne s'installent pas dans ce venv : ils tournent en conteneurs
(`docker/`), ce qui évite les incompatibilités Windows et garantit que le poste
exécute exactement les mêmes images que l'infrastructure déployée.

Deux fichiers de dépendances, aux rôles distincts :

| Fichier | Rôle |
|---|---|
| `requirements.txt` | Contraintes minimales lisibles (`fastapi>=0.115`) — ce qu'on installe |
| `requirements.lock.txt` | Versions exactes constatées (`pip freeze`) — repris tel quel par les images Docker pour garantir l'identité poste/production |

Le dossier `.venv/` n'est **jamais** versionné (lourd, propre à la machine,
régénérable) ; les deux fichiers de dépendances le sont **toujours** — ce sont
eux qui permettent de le reconstruire à l'identique.

## Conformité

- Chiffrement au repos (SSE/KMS) et en transit (TLS), IAM par rôles, secrets gérés.
- Pseudonymisation à l'ingestion, minimisation des variables publiées.
- Journalisation d'audit de chaque décision de scoring (traçabilité art. 22 RGPD).
- Contrôles qualité bloquants avant publication au feature store.

