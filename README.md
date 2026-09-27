# CrediScore — MLOps : infrastructure, pipelines et CI/CD

**Auteur :** Tagne Guylain Florian

Ce dépôt porte l'**industrialisation** du système de scoring : infrastructure as
code, pipelines de données, déploiement continu et supervision.

> Le développement de la solution IA elle-même est dans le dépôt complémentaire :
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
├── infra/              # Terraform : réseau, S3, VM, IAM, chiffrement
├── pipelines/          # DAGs Airflow + jobs PySpark
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

## Configuration

Aucun secret ne vit dans le dépôt. La configuration passe par trois fichiers
locaux, chacun accompagné d'un gabarit versionné qui documente ce qu'il faut
fournir :

| Fichier local | Gabarit versionné | Contenu |
|---|---|---|
| `docker/.env` | [`docker/.env.example`](docker/.env.example) | mots de passe des interfaces, nom du bucket, sel de pseudonymisation |
| `infra/terraform.tfvars` | `infra/terraform.tfvars.example` | IP administrateur, dimensionnement de la VM |
| `infra/backend.hcl` | `infra/backend.hcl.example` | bucket d'état Terraform (contient le n° de compte) |

Les trois sont exclus par [`.gitignore`](.gitignore). **Aucun identifiant AWS n'y
figure** : le poste s'authentifie par le profil SSO `crediscore`, la VM et l'API
par leur rôle IAM respectif (`infra/iam.tf`), et la CI par fédération OIDC. Le
même code fonctionne dans les trois cas — voir
[`docs/connexion-aws.md`](docs/connexion-aws.md) §7.

Démarrage de la pile, depuis `docker/` :

```bash
cp .env.example .env        # puis renseigner les valeurs
docker compose --env-file .env up -d
```

Le détail de chaque variable, de qui la lit et de ce qui se passe en son absence
est dans [`docs/connexion-aws.md`](docs/connexion-aws.md) §7.1. La cohérence
entre le gabarit, `docker-compose.yml` et le code est vérifiée à chaque `push`
par `tests/test_variables_environnement.py` : une variable proposée à
l'opérateur sans effet réel, ou attendue par la pile mais absente du gabarit,
fait échouer la CI.

## Conformité

- Chiffrement au repos (SSE/KMS) et en transit (TLS), IAM par rôles, secrets gérés.
- Pseudonymisation à l'ingestion, minimisation des variables publiées.
- Journalisation d'audit de chaque décision de scoring (traçabilité art. 22 RGPD).
- Contrôles qualité bloquants avant publication au feature store.

