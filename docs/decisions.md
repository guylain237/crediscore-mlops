# Journal des décisions d'architecte — CrediScore (infrastructure & MLOps)

---

## D-101 — 27/07/2026 — Démonstrateur à l'échelle, IaC dimensionnée par variables

- **Contexte :** l'architecture cible (cluster Spark 3 nœuds, PostgreSQL managé,
  K8s multi-pods) dépasse le budget et le délai d'un projet de certification de
  20 jours.
- **Options :** tout déployer à l'échelle cible ; tout simuler en local ;
  démonstrateur cloud réduit avec IaC paramétrée.
- **Choix :** démonstrateur cloud réel à échelle réduite — mêmes briques
  logicielles (Airflow, Spark, PostgreSQL, Prometheus/Grafana, K8s), ressources
  minimales, dimensionnement exposé en variables Terraform.
- **Raison :** un architecte dimensionne l'infrastructure au besoin et au coût ;
  tout ce qui est démontré en vidéo tourne réellement ; le passage à l'échelle
  cible est un changement de variables, pas de code.

## D-102 — 27/07/2026 — Les livrables code des Blocs 2 et 3 vivent dans ce dépôt

- **Contexte :** le référentiel exige du code IaC (Bloc 2) et du code de pipeline
  (Bloc 3) sur GitHub, plus deux dépôts distincts pour le Bloc 4.
- **Choix :** `infra/` (Bloc 2) et `pipelines/` (Bloc 3) dans ce dépôt n°2, aux
  côtés du CI/CD ; le dépôt n°1 reste dédié à la solution IA.
- **Raison :** infrastructure, pipeline et déploiement partagent le même cycle de
  vie opérationnel ; deux dépôts au total restent simples à présenter au jury,
  chaque bloc pointant vers un dossier précis.

## D-103 — 28/07/2026 — Environnement virtuel dédié, et conteneurs pour Airflow/Spark

- **Contexte :** poste Windows avec plusieurs Python installés ; Airflow ne
  supporte pas nativement Windows et Spark y demande une configuration lourde.
- **Options :** tout installer sur le poste ; WSL ; venv pour le code applicatif
  + conteneurs pour les moteurs.
- **Choix :** un `.venv` par dépôt pour le code applicatif (API, tests, outillage)
  alimenté par `requirements.txt` ; Airflow, Spark et PostgreSQL exclusivement en
  conteneurs (`docker/`).
- **Raison :** le poste exécute alors les mêmes images que l'infrastructure
  déployée — la démonstration au jury n'est pas un montage local mais l'exécution
  réelle des artefacts livrés. Les dépendances restent isolées et reproductibles.

## D-104 — 30/07/2026 — Identifiants AWS temporaires, et séparation opérateur / application

- **Contexte :** le poste doit piloter AWS (Terraform, dépôt des CSV) et l'API
  doit lire le data lake. Le référentiel exige « IAM par rôles, secrets gérés ».
- **Options :** clés d'accès longue durée par utilisateur IAM ; IAM Identity
  Center délivrant des identifiants temporaires ; une identité unique partagée
  entre l'humain et l'application.
- **Choix :** IAM Identity Center avec profil CLI nommé `crediscore` (session de
  4 h) pour l'**opérateur humain**, portée large car Terraform provisionne
  réseau, S3, KMS, IAM et K8s ; et un **rôle applicatif distinct** au moindre
  privilège, limité en lecture au préfixe `curated/` et en écriture à `audit/`.
  Aucun identifiant n'est passé au code : `boto3` résout la chaîne de credentials
  — profil sur le poste, rôle IAM en production. Clés d'accès admises en repli
  documenté uniquement.
- **Raison :** le moindre privilège s'applique à ce qui s'exécute en continu et
  est exposé, pas à l'acte de provisionnement. Un identifiant temporaire ne peut
  pas fuiter durablement, et le code reste identique du poste à la production,
  donc testable. Une compromission du conteneur de l'API ne donne accès ni aux
  données brutes ni à la suppression.
- **Suite :** GitHub Actions s'authentifiera par fédération OIDC, sans clé
  stockée dans les secrets du dépôt (Bloc 4). Procédure : `docs/connexion-aws.md`.
