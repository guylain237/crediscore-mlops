-- Bases créées au tout premier démarrage de PostgreSQL.
-- La base « crediscore » est créée par POSTGRES_DB ; on ajoute ici les deux
-- bases techniques, séparées pour que les métadonnées d'orchestration et de
-- suivi d'expériences ne se mélangent jamais aux données métier.
CREATE DATABASE airflow;
CREATE DATABASE mlflow;

COMMENT ON DATABASE airflow IS 'Métadonnées Apache Airflow (états des DAGs).';
COMMENT ON DATABASE mlflow  IS 'Métadonnées MLflow (expériences, registre de modèles).';
