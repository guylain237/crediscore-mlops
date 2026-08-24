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

## D-105 — 19/08/2026 — Un `apply` interrompu à mi-parcours devient un test

- **Contexte :** `terraform validate` déclare la configuration valide, puis
  `terraform apply` échoue après avoir créé 12 ressources sur 22 :
  `InvalidParameterValue: Invalid security group description`. Une apostrophe
  dans « l'IP administrateur » — AWS n'accepte, pour ces champs, que
  `a-zA-Z0-9 . _ - : / ( ) # , @ [ ] + = & ; { } ! $ *`, sans accent ni apostrophe.
- **Options :** corriger et retenir la règle ; ne plus écrire de description ;
  corriger **et** rendre la contrainte vérifiable automatiquement.
- **Choix :** correction, commentaire explicatif dans `network.tf`, et
  **garde-fou `tests/test_terraform_descriptions.py`** qui contrôle chaque
  description de groupe de sécurité contre le jeu de caractères autorisé.
  Le garde-fou a été vérifié par l'absurde : apostrophe réinjectée → test rouge,
  retirée → test vert.
- **Raison :** un `apply` interrompu laisse l'infrastructure dans un état partiel,
  le plus coûteux à diagnostiquer. Le contrôle coûte 0,2 seconde et s'exécute
  avant tout `apply`. C'est la même logique que le contrôle C-1 sur les variables
  sensibles : une règle qu'aucun test ne vérifie n'est qu'une intention.
- **Portée :** la restriction ne vaut que pour les groupes de sécurité et leurs
  règles. Les descriptions de rôles IAM acceptent l'apostrophe — vérifié : le
  rôle `api_scoring` a été créé sans erreur.

## D-106 — 22/08/2026 — La séparation des schémas comme mesure de non-discrimination

- **Contexte :** la note d'équité annonce que les attributs sensibles sont isolés
  et servent uniquement à l'audit. Restait à le rendre vrai dans la base.
- **Options :** une colonne « ne pas utiliser » dans la table des variables ;
  une convention de nommage ; des schémas séparés avec des droits distincts.
- **Choix :** quatre schémas — `entrepot`, `feature_store`, `audit_equite`,
  `journal`. Le genre et l'âge vivent dans `audit_equite`, jamais dans
  `feature_store`. De plus, `feature_store.registre_variables` porte une
  contrainte `CHECK (est_sensible = FALSE)` : la base refuse l'insertion d'une
  variable sensible au registre des variables servies.
- **Vérifié le 22/08 :** insertion d'une variable normale acceptée, insertion de
  `CODE_GENDER` rejetée par la contrainte. Le contrôle n'est pas déclaratif.
- **Raison :** une convention se contourne par oubli, une contrainte non. La
  non-discrimination devient une propriété du modèle physique, opposable en
  audit, et non une promesse dans un document.

## D-107 — 22/08/2026 — Le registre des variables comme pièce de conformité

- **Contexte :** l'AI Act et le RGPD demandent la minimisation et la traçabilité
  des données utilisées ; SHAP produit des noms de variables qu'un analyste doit
  pouvoir interpréter sans lire le code.
- **Choix :** `feature_store.registre_variables` (source, agrégat, type,
  description) et `feature_store.journal_publication` (run Airflow, SHA du
  commit, empreinte du lot, résultat des contrôles).
- **Raison :** une seule table répond à trois exigences distinctes — minimisation
  (ce qui n'est pas au registre n'est pas publié), explicabilité (d'un facteur
  SHAP à sa définition) et reproductibilité (quel code, quelles données, quel
  jour). Le coût est marginal, la valeur en soutenance est élevée.

## D-108 — 22/08/2026 — L'interrupteur du soir arrête la VM, il ne la détruit plus

- **Contexte :** `vm_active = false` posait `count = 0` sur l'instance. Avec
  `delete_on_termination = true`, l'extinction du soir emportait le disque
  racine — donc PostgreSQL, et avec lui le schéma en étoile, le feature store,
  la piste d'audit des décisions et les images Docker construites. Détecté au
  moment de lancer l'extinction, le plan annonçant « 1 to destroy ».
- **Options :** conserver la destruction et tout reconstruire chaque matin ;
  déporter l'état sur un volume EBS séparé persistant ; **arrêter** l'instance
  au lieu de la détruire.
- **Choix :** `aws_ec2_instance_state` piloté par `vm_active`. L'instance existe
  en permanence, elle est démarrée ou arrêtée. Adresse d'état déplacée par
  `terraform state mv` pour éviter un remplacement.
- **Vérifié :** plan du matin et plan du soir affichent tous deux
  **0 to destroy**.
- **Raison :** une instance arrêtée ne facture plus d'heures de calcul ; seul le
  disque subsiste, pour quelques centimes par jour. En face, la destruction
  imposait une demi-heure de remise en route chaque matin — dont une dizaine de
  minutes rien que pour reconstruire l'image Airflow (Java + PySpark). À
  quatorze jours de l'échéance, l'arbitrage n'a rien d'ambigu.
- **Ce qui n'est pas perdu :** la reproductibilité intégrale reste démontrable,
  et sera filmée une fois pour la vidéo du Bloc 2 — `terraform destroy` puis
  `terraform apply` reconstruit l'ensemble à partir du seul code.
- **Effet de bord assumé :** l'adresse IP publique change à chaque redémarrage.
  Relire `terraform output` le matin.

## D-109 — 24/08/2026 — Clé de déploiement en lecture seule plutôt qu'ouverture du dépôt

- **Contexte :** la VM devait récupérer le code. Le dépôt étant privé, `git clone`
  échouait, et les fichiers avaient été copiés par `tar` — une copie figée qui
  diverge du dépôt dès la première modification, sans qu'on s'en aperçoive.
- **Options :** rendre les dépôts publics ; poser un jeton personnel sur la VM ;
  garder la copie par `tar` ; **clé de déploiement en lecture seule**.
- **Choix :** une paire de clés générée **sur la VM** — la partie privée n'a
  jamais transité — dont la partie publique est déclarée sur GitHub comme clé de
  déploiement en lecture seule, limitée à `crediscore-mlops`.
- **Vérifié le 24/08 :** `ssh -T git@github.com` répond « Hi
  guylain237/crediscore-mlops », donc l'identité est le dépôt et non le compte ;
  `git pull` fonctionne ; `git push` est refusé — *The key you are
  authenticating with has been marked as read only*.
- **Raison — trois arguments, dans l'ordre :**
  1. **Traçabilité.** Ce qui tourne sur la VM correspond à un commit identifiable.
     Le SHA déployé se montre à l'écran pendant la démonstration : c'est la
     preuve que l'artefact filmé est bien celui du dépôt.
  2. **Surface d'attaque.** Un jeton personnel donnerait accès à tous les dépôts
     du compte, en écriture. Une clé de déploiement donne la lecture d'un seul
     dépôt. Si la VM est compromise, l'attaquant lit du code déjà destiné à
     devenir public — et rien d'autre.
  3. **Calendrier.** Les dépôts devront être ouverts au jury, mais le faire
     maintenant, à l'entrée des douze jours les plus chargés, exposerait
     publiquement la moindre erreur de commit. L'ouverture est reportée au 05/09,
     après l'audit final.
- **Préalable traité :** audit de l'historique des deux dépôts avant toute
  décision d'ouverture — aucun secret, aucune clé, aucun fichier de données ;
  une seule trouvaille, l'adresse IP du poste utilisée comme exemple dans un
  message d'erreur, retirée le 24/08. Elle subsiste dans le commit `4ce46d2` :
  à traiter par réécriture d'historique avant l'ouverture au public.
- **Conséquence pratique :** `git push` depuis le poste ne met plus à jour la VM
  tout seul. Le cycle est : commit, push, puis `scripts/mise-a-jour.sh` sur la
  VM, qui affiche le commit déployé.

## D-110 — 24/08/2026 — Deux règles d'alerte sur trois étaient muettes

- **Contexte :** avant de construire les tableaux de bord, contrôle des
  métriques réellement exposées par Prometheus. Deux des trois règles écrites le
  22/08 interrogeaient des séries inexistantes :
  - `DisqueBientotPlein` filtrait `mountpoint="/host"`. node-exporter tourne
    bien avec `--path.rootfs=/host`, mais publie le point de montage tel que le
    système le nomme, c'est-à-dire `/`. Requête : `"result":[]`. La règle ne
    pouvait **jamais** se déclencher, quel que soit l'état du disque.
  - `OrdonnanceurArrete` testait `up{job="airflow"} == 0`. Cette cible est
    statsd-exporter, pas Airflow : l'ordonnanceur pouvait mourir sans que rien
    ne s'allume. Remplacé par `airflow_scheduler_heartbeat`, métrique vérifiée
    présente.
- **Choix :** expressions corrigées, deux règles ajoutées (`ConteneurArrete`,
  `DagIllisible`), et chaque règle porte désormais en commentaire la manière de
  vérifier qu'elle n'est pas muette :
  `curl --get .../api/v1/query --data-urlencode 'query=<expr>'` — une réponse
  `"result":[]` signale une règle sans effet.
- **Vérifié :** les 6 règles sont chargées par Prometheus, état `ok`. Le
  déclenchement réel a été éprouvé en arrêtant un conteneur.
- **Raison :** une règle d'alerte qui ne s'est jamais déclenchée est
  indiscernable d'une règle qui ne peut pas se déclencher. C'est le même piège
  que le contrôle fantôme du D-003, transposé à la supervision : le silence
  passe pour de la sérénité alors qu'il n'est que de la surdité.
- **Latence assumée :** `ConteneurArrete` met environ huit minutes à se
  déclencher — cinq minutes avant que Prometheus considère la série périmée,
  puis les trois minutes du `for:`. Acceptable pour ce projet, et documenté
  plutôt que découvert par le jury.
- **Reste ouvert :** les alertes sont évaluées et visibles dans Grafana, mais
  pas encore routées vers un canal (courriel ou webhook). À traiter avant la
  vidéo du Bloc 3.
