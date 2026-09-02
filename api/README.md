# Où est l'API ?

Le code de l'API de scoring est dans l'autre dépôt : **`crediscore-ml/api/`**.

Ce n'est pas un oubli, c'est la séparation qui justifie d'avoir deux dépôts.

| Dépôt | Ce qu'il contient |
|---|---|
| `crediscore-ml` | la **solution d'IA** — le modèle, son seuil, ses explications, et l'API qui les sert |
| `crediscore-mlops` | **comment elle tourne** — infrastructure, pipeline, conteneurs, supervision, CI/CD |

L'API vit avec le modèle qu'elle sert : elle charge le même `modele_calibre.pkl`,
lit le même `configs/seuil_decision.yaml`, réutilise les mêmes libellés de
variables. Les séparer obligerait à dupliquer ces trois choses, donc à les voir
diverger.

## Ce que ce dépôt apporte à l'API

- **`docker/docker-compose.yml`**, service `api` — construit l'image depuis
  `crediscore-ml/Dockerfile`, lui branche PostgreSQL pour le journal des
  décisions (contrôle C-2) et lui passe le sel de pseudonymisation (C-7).
- **`pipelines/sql/01_schemas.sql`** — la table `journal.decisions` que l'API
  remplit.
- **`.github/workflows/`** — la chaîne qui construit et vérifie l'image.

## Démarrer l'API seule

```bash
cd ../crediscore-ml
docker build -t crediscore-api:1.0 .
docker run -p 8000:8000 \
  -v "$PWD/models:/app/models:ro" \
  -v "$PWD/../donnees_pipeline:/donnees_pipeline:ro" \
  crediscore-api:1.0

curl http://localhost:8000/sante
```

Sans `DATABASE_URL`, l'API écrit son journal dans un fichier local **et le dit
au démarrage**. C'est acceptable en développement, jamais en production.
