###############################################################################
# datalake.tf — le data lake, décrit par le code
#
# Les mêmes réglages qu'à l'étape 6 de docs/connexion-aws.md, mais exprimés
# en code : relisibles, versionnés,
# rejouables à l'identique, et auditables ligne par ligne.
#
# Rappel structurant : S3 n'a pas de dossiers. Les zones raw/, clean/ et
# curated/ ne sont PAS créées ici — ce sont des préfixes de noms d'objets, qui
# apparaissent dès qu'un objet y est écrit. Rien à provisionner pour elles.
###############################################################################

# `data` = lecture, pas création. Terraform interroge AWS : « à quel compte
# suis-je connecté ? ». Cela permet de construire le nom du bucket sans écrire
# le numéro de compte dans un dépôt public.
data "aws_caller_identity" "courant" {}

locals {
  # Un nom de bucket est unique AU MONDE (espace de noms mondial, pas par
  # compte) : le suffixer par le numéro de compte évite toute collision.
  nom_datalake = "${var.nom_projet}-datalake-${data.aws_caller_identity.courant.account_id}"

  # CONTRAT DES ZONES — déclaration unique dont tout le reste découle.
  #
  # S3 n'ayant pas de dossiers, une zone n'existe que par les droits qu'on lui
  # accorde. Cette carte est donc la seule source de vérité : la politique IAM
  # (iam.tf) et les sorties (outputs.tf) en sont dérivées. Ajouter une zone ici
  # suffit ; il devient impossible d'accorder un droit sur une zone non
  # déclarée, ou de déclarer une zone que personne ne peut atteindre.
  #
  # Niveaux d'accès :
  #   lecture   GetObject
  #   ecriture  GetObject + PutObject + DeleteObject
  #   ajout     PutObject SEUL — on écrit, on ne relit ni n'efface
  #   aucun     pas d'accès
  zones = {
    "raw" = {
      # Contient aussi le dictionnaire des colonnes : il est arrivé avec le jeu
      # de données et vit donc avec lui. Conséquence pour le pipeline : le
      # contrôle de complétude énumère les huit fichiers attendus au lieu de
      # compter les objets présents.
      objet = "Exports bruts des systèmes sources et dictionnaire des colonnes"
      vm    = "lecture"
      api   = "aucun"
    }
    "reference" = {
      # L'archive d'origine, telle qu'elle a été reçue, jamais décompressée ici.
      #
      # Ce n'est pas une simple sauvegarde : c'est une PREUVE D'ORIGINE. Son
      # empreinte permet d'établir que les CSV de `raw/` n'ont pas été altérés
      # entre la réception et le traitement — question qu'un auditeur pose
      # naturellement quand la donnée source commande des décisions de crédit.
      #
      # Le pipeline ne la lit jamais ; la VM y a accès en lecture pour pouvoir
      # recalculer cette empreinte lors d'une vérification.
      objet = "Archive d'origine du jeu de données — preuve d'intégrité"
      vm    = "lecture"
      api   = "aucun"
    }
    "clean" = {
      objet = "Données typées, dédoublonnées, attributs sensibles déviés"
      vm    = "ecriture"
      api   = "aucun"
    }
    "curated" = {
      objet = "Variables agrégées au grain du dossier"
      vm    = "ecriture"
      api   = "lecture"
    }
    "mlflow" = {
      objet = "Artefacts MLflow : modèles, graphiques SHAP, rapports d'équité"
      vm    = "ecriture"
      api   = "aucun"
    }
    "audit" = {
      # Le seul « ajout » du projet, et ce n'est pas un oubli : un journal
      # d'audit doit pouvoir être écrit, jamais relu ni effacé par celui qui
      # l'écrit. C'est la condition pour qu'il fasse foi en cas de contestation
      # d'une décision de crédit (politique P-7, AI Act art. 12).
      objet = "Journal d'audit des décisions de scoring"
      vm    = "aucun"
      api   = "ajout"
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Le bucket
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "datalake" {
  bucket = local.nom_datalake

  # false = `terraform destroy` ÉCHOUE si le bucket contient encore des
  # objets. C'est un garde-fou souhaité : les 2,5 Go de données brutes ne
  # peuvent pas disparaître par une commande tapée trop vite. Une suppression
  # réelle impose de vider le bucket sciemment.
  force_destroy = false
}

# ─────────────────────────────────────────────────────────────────────────────
# Versionnage — protège d'un écrasement ou d'une suppression accidentels
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket_versioning" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Chiffrement au repos — obligatoire sur toutes les zones
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket_server_side_encryption_configuration" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  rule {
    apply_server_side_encryption_by_default {
      # AES256 = SSE-S3, chiffrement géré par AWS, gratuit.
      #
      # Choix assumé face à SSE-KMS : KMS apporte une clé propre au projet,
      # avec sa politique d'accès et sa traçabilité d'usage dans CloudTrail —
      # mais coûte ~1 $/mois par clé, plus les appels. Pour un démonstrateur
      # sur free tier, SSE-S3 remplit l'exigence « chiffrement au repos » à
      # coût nul. Le passage à KMS est un changement de deux lignes ici, sans
      # impact sur le reste du code : c'est une décision de coût, pas
      # d'architecture.
      sse_algorithm = "AES256"
    }

    # Réduit les appels de déchiffrement facturés côté KMS le jour où l'on y
    # passe. Sans effet avec SSE-S3, mais correct par anticipation.
    bucket_key_enabled = true
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Blocage de tout accès public — un data lake de données de crédit ne s'expose
# jamais sur Internet, à aucune condition
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket_public_access_block" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  # Ces quatre verrous sont actifs par défaut sur tout nouveau bucket depuis
  # 2023. Les déclarer ne change donc pas l'état : cela rend le choix
  # VOLONTAIRE et AUDITABLE, au lieu d'être un défaut hérité. C'est cette
  # différence que vérifie un audit.
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

# ─────────────────────────────────────────────────────────────────────────────
# Hygiène de stockage — deux règles qui évitent une facture silencieuse
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket_lifecycle_configuration" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  # La purge des anciennes versions n'a de sens qu'une fois le versionnage
  # actif : on ordonne explicitement les deux, sinon Terraform peut les créer
  # en parallèle et la règle échouerait.
  depends_on = [aws_s3_bucket_versioning.datalake]

  rule {
    id     = "abandonner-les-envois-incomplets"
    status = "Enabled"

    filter {} # vide = s'applique à tous les objets

    # Un fichier de plus de ~100 Mo est envoyé par morceaux (multipart). Si le
    # transfert casse — coupure réseau pendant les 2,5 Go de CSV — les morceaux
    # déjà envoyés RESTENT stockés et facturés, tout en étant invisibles dans
    # la console. C'est un coût fantôme classique. Cette règle les balaie.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "purger-les-anciennes-versions"
    status = "Enabled"

    filter {}

    # Le versionnage garde chaque version écrasée, et chacune est facturée.
    # Deux `sync` de 2,5 Go sans purge = 5 Go stockés, donc le free tier
    # dépassé pour rien. On garde de quoi revenir en arrière, pas plus.
    noncurrent_version_expiration {
      noncurrent_days = var.jours_conservation_versions
    }
  }
}
