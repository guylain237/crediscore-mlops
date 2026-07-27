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
