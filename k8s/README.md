# Pourquoi il n'y a pas de manifestes Kubernetes

**Décision du 02/09/2026 — application de la règle de coupe.**

Le plan d'action prévoyait un déploiement sur k3s avec un `HorizontalPodAutoscaler`.
Il prévoyait aussi, à son §5, ce qu'il faudrait sacrifier si le calendrier
glissait, et dans quel ordre. **k3s et l'autoscaling étaient le premier élément
de cette liste.** Le seuil d'alerte du plan a été franchi le 27/08.

La coupe a donc été appliquée comme prévue, pas improvisée.

## Ce qui la remplace

Le service `api` de `docker/docker-compose.yml`. Il porte les mêmes propriétés
que celles qu'un déploiement Kubernetes aurait démontrées :

| Propriété | Où elle est tenue |
|---|---|
| Image reproductible | `crediscore-ml/Dockerfile`, versions épinglées |
| Sonde de vivacité | `HEALTHCHECK` sur `/sante` — vérifie que le **modèle** est chargé, pas seulement que le processus vit |
| Redémarrage automatique | `restart: unless-stopped` |
| Configuration hors image | variables d'environnement, modèle monté en volume |
| Exécution sans privilège | utilisateur `crediscore`, uid 10001 |
| Mise à l'échelle | `docker compose up --scale api=3` |

## Ce que la coupe fait vraiment perdre

Il faut le dire précisément plutôt que de prétendre que c'est équivalent :

- **la mise à l'échelle automatique** sous charge — ici elle est manuelle ;
- **la répartition sur plusieurs machines** — tout tient sur une VM (décision D-101) ;
- **les mises à jour progressives** sans interruption.

Aucune de ces trois ne change quoi que ce soit à la conformité du système ni à
la traçabilité d'une décision, qui sont l'objet du projet. C'est pourquoi elles
figuraient en tête de la liste des sacrifices, et non à sa fin.
