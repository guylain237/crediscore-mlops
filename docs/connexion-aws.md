# Connecter le poste et l'application à AWS

Procédure à suivre une fois, de bout en bout, sur un poste Windows. À l'arrivée :
le poste peut piloter AWS (Terraform, upload S3) et l'application peut lire le
data lake **sans qu'aucune clé secrète n'existe dans le code ni dans le dépôt**.

**Prérequis déjà faits :** compte AWS créé, alerte budget active (J1 du plan).
**Point de départ réel constaté :** AWS CLI non installé, `%USERPROFILE%\.aws` vide.

---

## Le principe à retenir avant de commencer

Une application ne « contient » jamais ses identifiants AWS. Elle demande au SDK
(`boto3`) de les trouver, et le SDK parcourt une **chaîne de credentials**, dans
cet ordre :

| Rang | Source | Utilisée dans ce projet pour |
|---|---|---|
| 1 | Variables d'environnement (`AWS_ACCESS_KEY_ID`, …) | rien — à éviter |
| 2 | Fichier partagé `~/.aws/credentials` + profil | ton poste de développement |
| 3 | Credentials de conteneur / instance (rôle IAM) | l'API en production (K8s, EC2) |

Le code est **identique** dans les trois cas : `boto3.client("s3")`. Seule la
source des identifiants change selon l'endroit où le code tourne. C'est ce qui
permet d'écrire « secrets gérés, IAM par rôles » dans les livrables sans mentir.

Trois interdits, qui sont aussi des points d'audit du Bloc 2 :

- **jamais** de clé d'accès écrite dans un `.py`, un `.tf`, un `Dockerfile` ou un notebook ;
- **jamais** la clé du compte *root* (le compte de facturation ne travaille pas) ;
- **jamais** de `.env` ni de `.tfvars` committé — les deux sont déjà dans [`.gitignore`](../.gitignore).

---

## Étape 1 — Verrouiller le compte root (5 min, à faire d'abord)

Dans la console AWS, connecté en root :

1. **MFA obligatoire** : *Compte → Informations d'identification de sécurité → MFA → Attribuer un dispositif MFA* (application d'authentification du téléphone).
2. **Supprimer toute clé d'accès root** si la section « Clés d'accès » en affiche une. Une clé root ne peut pas être restreinte : elle donne tout, y compris la fermeture du compte.
3. Refermer la session root. Tu n'y reviendras que pour la facturation.

> À montrer dans la vidéo Bloc 2 : la page *Informations d'identification de
> sécurité* avec MFA activé et zéro clé d'accès root.

## Étape 2 — Créer ton identité de travail

Deux chemins possibles. Le premier est celui à retenir pour ce projet.

### Option A — IAM Identity Center (recommandée)

Elle délivre des identifiants **temporaires** (quelques heures), renouvelés par
une simple connexion navigateur. Aucun secret durable ne se pose sur ton disque.

1. Console → service **IAM Identity Center** → *Activer*. Choisis la région
   **`eu-north-1` (Stockholm)** comme région de l'annuaire.
2. *Utilisateurs* → *Ajouter un utilisateur* : ton nom, ton e-mail (tu recevras
   une invitation à définir le mot de passe).
3. *Jeux d'autorisations* → *Créer* → jeu d'autorisations prédéfini
   **`AdministratorAccess`**, nomme-le `CrediScore-Operateur`, durée de session **4 heures**.
4. *Comptes AWS* → sélectionne ton compte → *Attribuer des utilisateurs* → ton
   utilisateur + le jeu `CrediScore-Operateur`.
5. Note l'**URL du portail d'accès** affichée sur le tableau de bord
   (`https://d-xxxxxxxxxx.awsapps.com/start`) — elle sert à l'étape 4.

Pourquoi `AdministratorAccess` ici alors que le référentiel exige le moindre
privilège ? Parce que cette identité est celle de **l'opérateur humain qui
provisionne** : Terraform doit créer VPC, S3, KMS, IAM, RDS, EKS — restreindre
finement reviendrait à réécrire une politique d'administration. Le moindre
privilège s'applique à l'**identité de l'application**, créée à l'étape 7, qui
n'a droit qu'à deux préfixes S3. Cette séparation opérateur / exécution est
exactement ce qu'on attend d'un architecte, et se défend en une phrase.

### Option B — Utilisateur IAM avec clés d'accès (repli)

Plus rapide, mais la clé est un secret durable posé sur ton disque : à n'utiliser
que si l'option A bloque.

1. IAM → *Utilisateurs* → *Créer un utilisateur* : `crediscore-operateur`, **sans** accès console.
2. Attacher la politique `AdministratorAccess`.
3. Onglet *Informations d'identification de sécurité* → *Créer une clé d'accès*
   → cas d'usage « Interface de ligne de commande (CLI) ».
4. Copier la clé **et** le secret : le secret n'est affiché qu'une seule fois.
5. Activer MFA sur cet utilisateur aussi.
6. Rappel : cette clé se supprime et se recrée en 30 secondes. Au moindre doute
   de fuite (capture d'écran, commit, partage), supprime-la immédiatement.

## Étape 3 — Installer AWS CLI v2 sur le poste

```powershell
winget install -e --id Amazon.AWSCLI
```

Si `winget` n'est pas disponible :

```powershell
msiexec.exe /i https://awscli.amazonaws.com/AWSCLIV2.msi
```

**Ferme puis réouvre le terminal** (le `PATH` n'est pas rafraîchi dans une session
déjà ouverte), et vérifie :

```powershell
aws --version
```

Tu dois lire `aws-cli/2.x.x`. Si la commande reste inconnue après réouverture,
le `PATH` n'a pas été mis à jour — voir la section Dépannage.

## Étape 4 — Configurer le profil `crediscore`

Un **profil nommé** plutôt que le profil par défaut : ta machine pourra héberger
plusieurs contextes AWS sans qu'une commande parte sur le mauvais compte.

### Si tu as choisi l'option A (Identity Center)

```powershell
aws configure sso --profile crediscore
```

Réponses attendues :

| Question | Réponse |
|---|---|
| `SSO session name` | `crediscore` |
| `SSO start URL` | l'URL du portail notée à l'étape 2 |
| `SSO region` | `eu-north-1` |
| `SSO registration scopes` | laisser la valeur par défaut (Entrée) |

Le navigateur s'ouvre pour l'autorisation ; la CLI propose ensuite le compte et
le jeu d'autorisations `CrediScore-Operateur`. Termine avec la région par défaut
**`eu-north-1`** et le format de sortie **`json`**.

Ensuite, une fois par journée de travail :

```powershell
aws sso login --profile crediscore
```

### Si tu as choisi l'option B (clés d'accès)

```powershell
aws configure --profile crediscore
```

Colle la clé, le secret, puis `eu-north-1` et `json`.

### Pourquoi `eu-north-1` (Stockholm) — la région du projet

Les données de `application_train.csv` sont des données de crédit à caractère
personnel. Les héberger en région européenne évite tout transfert hors UE à
justifier au titre du RGPD — argument à réutiliser tel quel dans le plan de
gouvernance (Bloc 1). Stockholm ajoute un argument de coût : c'est l'une des
régions les moins chères d'Europe, sensiblement moins que Paris sur EC2 et S3,
ce qui compte pour un démonstrateur mené sur le free tier.

Une seule règle, mais impérative : **la même région partout** — profil CLI,
commandes `aws`, variables Terraform, bucket. Une commande lancée depuis une
autre région ne voit pas le bucket et renvoie une erreur trompeuse
(`NoSuchBucket`, `IllegalLocationConstraintException`) alors que tout est
correct par ailleurs. C'est la première cause de perte de temps sur S3.

## Étape 5 — Vérifier que la connexion fonctionne

```powershell
aws sts get-caller-identity --profile crediscore
```

Une réponse JSON avec `Account`, `UserId` et `Arn` prouve que le poste est
authentifié. C'est **la** commande de diagnostic : tant qu'elle échoue, rien
d'autre ne marchera, et inutile de chercher ailleurs.

Confort : déclarer le profil pour toute la session évite de répéter `--profile`.

```powershell
$env:AWS_PROFILE = "crediscore"
```

Pour le rendre permanent (à relancer une seule fois) :

```powershell
[Environment]::SetEnvironmentVariable("AWS_PROFILE", "crediscore", "User")
```

## Étape 6 — Amorçage : le seul bucket à créer à la main

### Pourquoi un bucket à la main alors que tout doit être en Terraform

Terraform garde l'état de l'infrastructure (`tfstate`) dans un bucket S3
(étape 8). Mais ce bucket ne peut pas être créé par le Terraform qui s'en sert
pour démarrer : il faut qu'il existe avant le premier `terraform init`. Ce
problème d'amorçage se résout partout de la même façon — **un** bucket d'état
créé à la main, une fois, et tout le reste en IaC.

D'où **deux buckets, aux rôles bien distincts** :

| Bucket | Contenu | Créé par |
|---|---|---|
| `crediscore-tfstate-<compte>` | l'état Terraform, rien d'autre | à la main, maintenant |
| `crediscore-datalake-<compte>` | les zones `raw/`, `clean/`, `curated/` | **Terraform**, étape 8 |

Ne crée donc **pas** le bucket du data lake à la main : Terraform échouerait
ensuite sur `BucketAlreadyOwnedByYou` en tentant de créer une ressource qui
existe déjà hors de son état. Le data lake est le livrable auditable du Bloc 2 —
il doit naître du code.

### Créer le bucket d'état

Le nom d'un bucket est unique **au monde** (c'est un espace de noms mondial, pas
un espace par compte) : suffixe-le avec ton numéro de compte, visible dans la
sortie de l'étape 5.

```powershell
$compte = (aws sts get-caller-identity --query Account --output text)
$tfstate = "crediscore-tfstate-$compte"

aws s3api create-bucket --bucket $tfstate --region eu-north-1 `
  --create-bucket-configuration LocationConstraint=eu-north-1

# Versionnage : indispensable ici — permet de revenir à un état antérieur
aws s3api put-bucket-versioning --bucket $tfstate `
  --versioning-configuration Status=Enabled

# Chiffrement au repos (exigence Bloc 2)
aws s3api put-bucket-encryption --bucket $tfstate `
  --server-side-encryption-configuration '{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"AES256\"}}]}'

# Blocage de tout accès public
aws s3api put-public-access-block --bucket $tfstate --public-access-block-configuration `
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws s3api get-bucket-versioning --bucket $tfstate     # doit afficher Enabled
```

Le versionnage n'est pas décoratif sur ce bucket : le `tfstate` est le seul point
de vérité sur ce qui existe réellement dans le compte. Corrompu ou écrasé, tu
perds la capacité de détruire proprement — donc de maîtriser la facture.

À propos des trois dernières commandes : le chiffrement SSE-S3 et le blocage des
accès publics sont désormais **actifs par défaut** sur tout nouveau bucket. Les
déclarer explicitement ne change donc pas l'état, mais rend le réglage
**volontaire et auditable** plutôt que subi — c'est précisément ce qu'un
audit vérifie, et ce sont les mêmes attributs qu'on retrouvera dans le code
Terraform.

## Étape 7 — Connecter l'application (moindre privilège)

Tout ce qui suit s'écrit dès maintenant : le code n'a pas besoin que le bucket
existe pour être rédigé. Seules deux choses attendent l'étape 8 — la valeur de
`DATALAKE_BUCKET` dans le `.env`, qui sortira de `terraform output`, et le test
d'intégration 7.4, qui ne passera qu'une fois le bucket créé.

### 7.1 Ajouter le SDK aux dépendances

Dans [`requirements.txt`](../requirements.txt), sous une rubrique « Accès au data lake » :

```
boto3>=1.35
```

puis, **venv du dépôt activé** (prompt `(.venv)`, décision D-103) :

```powershell
python -m pip install -r requirements.txt
```

### 7.2 Le code ne connaît que des noms, jamais des secrets

Configuration lue depuis l'environnement, avec `pydantic` déjà présent au projet :

```python
# api/config.py
from pydantic_settings import BaseSettings

class Reglages(BaseSettings):
    aws_region: str = "eu-north-1"
    datalake_bucket: str            # obligatoire : aucune valeur par défaut
    prefixe_curated: str = "curated/"

reglages = Reglages()
```

```python
# api/datalake.py
import boto3
from .config import reglages

def client_s3():
    """Aucun identifiant en argument : boto3 résout la chaîne de credentials.

    Sur le poste → profil `crediscore` ; en production → rôle IAM du pod.
    Le code est le même, et c'est tout l'intérêt.
    """
    return boto3.client("s3", region_name=reglages.aws_region)
```

Fichier `.env` **local** (déjà ignoré par git) :

```
DATALAKE_BUCKET=crediscore-datalake-123456789012
AWS_REGION=eu-north-1
```

Committe un `.env.example` avec les mêmes clés et des valeurs vides : il
documente ce qu'il faut fournir sans divulguer ton numéro de compte.

### 7.3 Le rôle de l'application, restreint à deux préfixes

Ici s'applique le moindre privilège. Politique IAM à attacher au rôle porté par
l'API en production (`crediscore-api-role`) :

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "LireLesDonneesExploitables",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::crediscore-datalake-123456789012",
        "arn:aws:s3:::crediscore-datalake-123456789012/curated/*"
      ]
    },
    {
      "Sid": "EcrireLesJournauxDAudit",
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::crediscore-datalake-123456789012/audit/*"
    }
  ]
}
```

L'API de scoring ne lit que `curated/` et n'écrit que ses journaux d'audit : elle
ne peut ni toucher aux données brutes, ni supprimer quoi que ce soit. Une
compromission du conteneur ne donne pas le data lake.

Cette politique et ce rôle seront écrits en Terraform dans `infra/` : la console
sert à comprendre, l'IaC est le livrable auditable (décision D-102).

### 7.4 Vérification automatisée, dans la convention du dépôt

```python
# tests/test_aws_connexion.py
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATALAKE_BUCKET"),
    reason="Test d'intégration : nécessite un profil AWS et DATALAKE_BUCKET.",
)

def test_le_bucket_du_datalake_est_accessible() -> None:
    import boto3
    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "eu-north-1"))
    s3.head_bucket(Bucket=os.environ["DATALAKE_BUCKET"])
```

Le `skipif` est important : la CI publique n'a pas d'identifiants AWS, et un test
d'intégration ne doit jamais faire échouer un pipeline pour cette raison.

## Étape 8 — Terraform : l'infrastructure décrite par le code

### 8.1 Les fichiers, et ce que chacun porte

Le code vit dans [`infra/`](../infra/) et est commenté ligne par ligne. Aucun
extrait n'est recopié ici : le code est la source de vérité, cette doc explique
la démarche.

| Fichier | Rôle | Versionné |
|---|---|---|
| `providers.tf` | version de Terraform, provider AWS, étiquetage automatique | oui |
| `variables.tf` | tous les points de réglage (région, dimensionnement, rétention) | oui |
| `backend.tf` | où stocker l'état — configuration **partielle** | oui |
| `datalake.tf` | le bucket du data lake et ses quatre garanties | oui |
| `outputs.tf` | les valeurs rendues à l'extérieur (nom du bucket, zones) | oui |
| `backend.hcl` | le nom du bucket d'état — contient le n° de compte | **non** |
| `terraform.tfvars` | surcharges locales éventuelles | **non** |
| `.terraform.lock.hcl` | versions et empreintes exactes des providers | **oui** — voir ci-dessous |

Deux fichiers d'exemple (`backend.hcl.example`, `terraform.tfvars.example`)
documentent ce qu'il faut fournir sans divulguer de valeur.

Le fichier de verrouillage `.terraform.lock.hcl` se versionne, au même titre que
`requirements.lock.txt` côté Python : il garantit que la CI et ton poste
utilisent exactement le même provider. L'ignorer reviendrait à exiger la
reproductibilité d'un côté et à y renoncer de l'autre.

### 8.2 L'authentification : rien de figé dans le code

Ni le provider ni le backend ne mentionnent `profile`. Tous deux suivent alors la
même chaîne de recherche que `boto3` : `AWS_PROFILE` → `~/.aws/config` → rôle IAM
de la machine. Le même code fonctionne donc sur ton poste et dans GitHub Actions,
où les identifiants viendront d'un rôle assumé par OIDC. Écrire
`profile = "crediscore"` dans le code casserait la CI, puisque ce profil n'existe
que sur ta machine.

### 8.3 Le backend en configuration partielle

Le bloc `backend` est lu **avant** l'évaluation des variables : il n'accepte ni
`var.*`, ni interpolation — tout doit être littéral. Or le nom du bucket d'état
contient ton numéro de compte, qui n'a pas à figurer dans un dépôt public.

La parade prévue par Terraform est la configuration partielle : on omet la valeur
à protéger, et on la fournit à l'initialisation.

```powershell
cd infra
Copy-Item backend.hcl.example backend.hcl   # puis renseigner le nom du bucket
terraform init "-backend-config=backend.hcl"
```

**Les guillemets sont obligatoires sous PowerShell.** Sans eux, PowerShell
découpe l'argument au niveau du `=` et Terraform reçoit deux arguments au lieu
d'un — d'où l'erreur déroutante `Too many command line arguments. Did you mean
to use -chdir?`. Les guillemets forcent le passage en un seul bloc. La variante
`terraform --% init -backend-config=backend.hcl` fonctionne aussi : `--%` dit à
PowerShell de ne plus rien interpréter de la ligne.

Cette option n'est nécessaire qu'à l'`init`. Ensuite, Terraform la mémorise dans
`.terraform/` et les commandes suivantes n'en ont plus besoin — `plan` et `apply`
ne prennent aucun argument, donc aucun risque de ce côté.

### 8.4 Appliquer

```powershell
terraform fmt -check    # mise en forme canonique
terraform validate      # cohérence de la syntaxe et des types
terraform plan          # SIMULATION — ne modifie rien
terraform apply         # exécution réelle
```

`plan` est le moment important : il annonce la liste exacte des créations,
modifications et suppressions. Prends l'habitude de le lire ligne à ligne —
c'est là qu'on repère un `destroy` involontaire avant qu'il ne s'exécute. Sur ce
`plan`-ci, tu dois lire `Plan: 5 to add, 0 to change, 0 to destroy` — le bucket
et ses quatre configurations. La lecture `data.aws_caller_identity` n'y apparaît
pas : elle lit, elle ne crée rien.

Le nom du bucket n'est pas écrit dans le code : il est construit à l'exécution à
partir du numéro de compte lu par `data.aws_caller_identity`. Récupère-le après
l'`apply` :

```powershell
terraform output -raw datalake_bucket
```

### 8.5 Déposer les sources en zone brute

Le bucket existe maintenant ; on peut le remplir. Le dossier `input/` contient
10 CSV pour **2,5 Go**, dont seuls 8 sont des tables de données ; les deux autres
(dictionnaire de colonnes, exemple de soumission Kaggle) ne sont pas des sources
et vont dans un préfixe distinct.

```powershell
$bucket = (terraform output -raw datalake_bucket)
$input  = "C:\Users\guyla\OneDrive\Desktop\projetMLDIPLOME\input"

# Les 8 tables sources → zone brute
aws s3 sync $input "s3://$bucket/raw/" `
  --exclude "*" --include "*.csv" `
  --exclude "HomeCredit_columns_description.csv" --exclude "sample_submission.csv"

# Le dictionnaire de colonnes → référence (ce n'est pas une donnée)
aws s3 cp "$input\HomeCredit_columns_description.csv" "s3://$bucket/reference/"

aws s3 ls "s3://$bucket/raw/" --human-readable
```

Dans les filtres de `sync`, l'ordre compte : chaque règle s'applique après la
précédente. On part de « rien » (`--exclude "*"`), on rouvre aux CSV, puis on
retire nominativement les deux intrus.

`sync` est idempotent : relancé, il ne transfère que ce qui a changé — un
transfert interrompu se reprend donc sans tout réenvoyer. Compte 10 à 40 minutes
selon ta bande passante montante (le trafic entrant vers S3 est gratuit, et
2,5 Go tiennent dans les 5 Go du free tier S3).

Les trois zones `raw/`, `clean/`, `curated/` sont de simples préfixes — S3 n'a
pas de dossiers, et c'est le pipeline qui écrira les deux suivantes. Les données
ne sont **jamais** déposées par Terraform : l'IaC décrit le contenant, le
pipeline gère le contenu. Mélanger les deux rendrait un `terraform destroy`
capable d'effacer tes données.

## Étape 9 — Ne pas laisser filer la facture

| Garde-fou | Commande / emplacement |
|---|---|
| Alerte budget | Console → *Billing → Budgets* (déjà en place — vérifie le seuil et l'e-mail) |
| Voir ce qui tourne | Console → *Cost Explorer*, filtré sur le tag `Projet = CrediScore` |
| Éteindre entre deux sessions | `terraform destroy` sur les ressources facturées à l'heure (EC2, RDS, EKS, NAT Gateway) |
| Ce qu'on garde | Le bucket S3 : quelques centimes par mois, et il contient l'état Terraform |

Le poste à surveiller en priorité est la **NAT Gateway** (~32 €/mois, facturée à
l'heure même inutilisée) : c'est le premier dérapage de facture sur ce type
d'architecture. Détruire l'infra le soir et la recréer le matin est précisément
ce que l'IaC rend possible — et c'est un argument à faire valoir, pas une
bricole.

---

## Dépannage

| Symptôme | Cause | Correction |
|---|---|---|
| `aws : terme non reconnu` après installation | `PATH` non rafraîchi | Fermer et réouvrir le terminal ; sinon `$env:Path += ";C:\Program Files\Amazon\AWSCLIV2"` |
| `Unable to locate credentials` | Aucun profil résolu | `$env:AWS_PROFILE = "crediscore"` puis `aws sts get-caller-identity` |
| `The SSO session has expired` | Session temporaire échue (normal) | `aws sso login --profile crediscore` |
| `InvalidAccessKeyId` / `SignatureDoesNotMatch` | Clé erronée, révoquée, ou espace parasite collé | Recréer la clé (option B, étape 2) |
| `BucketAlreadyExists` | Nom déjà pris mondialement | Suffixer avec le n° de compte |
| `IllegalLocationConstraintException` | Région de la commande ≠ région du bucket | Ajouter `--region eu-north-1` |
| `AccessDenied` sur `s3 sync` | Politique trop restrictive | Tester d'abord avec le profil opérateur ; la restriction ne concerne que le rôle applicatif |
| `NoCredentialProviders` dans Terraform | aucun identifiant résolu | `$env:AWS_PROFILE = "crediscore"` puis `aws sso login` |
| `Too many command line arguments. Did you mean to use -chdir?` | PowerShell a découpé l'argument au `=` | Encadrer de guillemets : `"-backend-config=backend.hcl"` |
| `Backend initialization required` | l'`init` précédent a échoué | Relancer l'`init` corrigé — ce n'est pas un problème distinct |
| `Invalid reference` sur une étiquette | valeur texte écrite sans guillemets en HCL | `= "crediscore"` et non `= crediscore` |
| Boto3 marche en CLI mais pas dans le venv | Mauvais interpréteur | Vérifier `(.venv)` puis `pytest tests\test_environment.py -q` |

## Récapitulatif

- [ ] MFA sur le root, aucune clé d'accès root
- [ ] Identité de travail créée (Identity Center de préférence)
- [ ] AWS CLI v2 installé, `aws --version` répond
- [ ] Profil `crediscore` configuré sur `eu-north-1`
- [ ] `aws sts get-caller-identity --profile crediscore` répond
- [ ] Bucket d'**état** créé à la main : versionné, chiffré, accès public bloqué
- [ ] `terraform init` puis `apply` réussis → bucket **data lake** créé par le code
- [ ] Les 8 CSV sous `raw/`, le dictionnaire de colonnes sous `reference/`
- [ ] `boto3` dans `requirements.txt`, `.env` local + `.env.example` committé
- [ ] Politique du rôle applicatif limitée à `curated/` et `audit/`
- [ ] Alerte budget vérifiée

## Pour la suite (Bloc 4)

GitHub Actions ne recevra **aucune clé AWS**. Le mécanisme à utiliser est la
fédération **OIDC** : un rôle IAM qui accorde sa confiance au dépôt GitHub, et
des identifiants temporaires émis à chaque exécution. Aucun secret durable dans
les *repository secrets*, donc rien à faire fuiter ni à faire tourner. À traiter
au moment du pipeline de déploiement.
