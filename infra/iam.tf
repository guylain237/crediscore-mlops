###############################################################################
# iam.tf — les identités des machines, au moindre privilège
#
# Contrôle C-9 du plan de gouvernance (crediscore-ml/docs/gouvernance.md),
# application de la politique P-8.
#
# DEUX identités distinctes, et c'est tout l'enjeu :
#
#   1. `vm_traitement` — la VM qui exécute Airflow et Spark. Elle doit LIRE les
#      données brutes et ÉCRIRE les zones intermédiaires. C'est un rôle large,
#      mais porté par une machine que l'on maîtrise.
#
#   2. `api_scoring` — l'API exposée aux utilisateurs. Elle ne peut LIRE que
#      `curated/` et n'ÉCRIRE que `audit/`. Elle ne voit jamais les données
#      brutes et ne peut rien supprimer.
#
# Pourquoi les séparer : l'API est le composant exposé, donc le plus susceptible
# d'être compromis. Si elle portait les droits de la VM, une faille dans l'API
# donnerait l'ensemble du data lake. Cette séparation est ce qu'on attend
# quand le référentiel dit « IAM par rôles ».
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# Rôle 1 — la VM de traitement
# ─────────────────────────────────────────────────────────────────────────────

# « Qui a le droit d'endosser ce rôle ? » — ici, le service EC2, c'est-à-dire
# une machine virtuelle. Aucun humain, aucune clé d'accès.
data "aws_iam_policy_document" "confiance_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "vm_traitement" {
  name               = "${var.nom_projet}-vm-traitement"
  description        = "Role de la VM Airflow/Spark : lecture raw, ecriture clean et curated"
  assume_role_policy = data.aws_iam_policy_document.confiance_ec2.json
}

locals {
  # Dérivé du contrat des zones (datalake.tf). Aucune liste de préfixes n'est
  # réécrite à la main ici : la politique ne peut donc pas diverger du contrat.
  vm_lecture  = [for zone, contrat in local.zones : zone if contrat.vm == "lecture"]
  vm_ecriture = [for zone, contrat in local.zones : zone if contrat.vm == "ecriture"]
  vm_visibles = [for zone, contrat in local.zones : zone if contrat.vm != "aucun"]
  api_lecture = [for zone, contrat in local.zones : zone if contrat.api == "lecture"]
  api_ajout   = [for zone, contrat in local.zones : zone if contrat.api == "ajout"]
}

data "aws_iam_policy_document" "droits_traitement" {
  # Lister le bucket est une action de NIVEAU BUCKET : elle ne peut pas être
  # restreinte à un préfixe par la ressource, on la restreint par condition.
  statement {
    sid       = "ListerLesZonesAutorisees"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.datalake.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for zone in local.vm_visibles : "${zone}/*"]
    }
  }

  # Zones en lecture seule : les données sources restent infalsifiables, y
  # compris par la machine qui les traite.
  statement {
    sid       = "LireLesZonesSources"
    actions   = ["s3:GetObject"]
    resources = [for zone in local.vm_lecture : "${aws_s3_bucket.datalake.arn}/${zone}/*"]
  }

  # Zones que le pipeline produit : il doit pouvoir les relire, les réécrire
  # — l'idempotence l'exige — et les nettoyer.
  statement {
    sid       = "EcrireLesZonesProduites"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [for zone in local.vm_ecriture : "${aws_s3_bucket.datalake.arn}/${zone}/*"]
  }

  # La VM peut endosser le rôle de l'API — utile lorsque le conteneur de
  # scoring tournera sur cette même machine. Le droit est explicite et limité
  # à ce seul rôle : la VM ne peut pas endosser n'importe quelle identité.
  statement {
    sid       = "EndosserLeRoleApplicatif"
    actions   = ["sts:AssumeRole"]
    resources = [aws_iam_role.api_scoring.arn]
  }
}

resource "aws_iam_role_policy" "droits_traitement" {
  name   = "${var.nom_projet}-droits-traitement"
  role   = aws_iam_role.vm_traitement.id
  policy = data.aws_iam_policy_document.droits_traitement.json
}

# Session Manager : permet de se connecter à la VM SANS ouvrir le port 22 et
# sans clé SSH, avec journalisation des sessions. Gratuit. On garde SSH en
# parallèle pour le confort, mais SSM est la voie recommandée en production —
# argument à citer en revue d'architecture.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.vm_traitement.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Un « instance profile » est l'enveloppe qui permet d'attacher un rôle IAM à
# une instance EC2. C'est une contrainte technique d'AWS, pas un concept
# supplémentaire : un rôle ne s'attache pas directement à une machine.
resource "aws_iam_instance_profile" "vm_traitement" {
  name = "${var.nom_projet}-vm-traitement"
  role = aws_iam_role.vm_traitement.name
}

# ─────────────────────────────────────────────────────────────────────────────
# Rôle 2 — l'API de scoring, au moindre privilège strict
# ─────────────────────────────────────────────────────────────────────────────

# Ce rôle n'est endossable QUE par la VM de traitement : ni par un humain, ni
# par une autre machine du compte.
data "aws_iam_policy_document" "confiance_api" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.vm_traitement.arn]
    }
  }
}

resource "aws_iam_role" "api_scoring" {
  name               = "${var.nom_projet}-api-scoring"
  description        = "Role de l'API : lecture curated uniquement, ecriture audit uniquement"
  assume_role_policy = data.aws_iam_policy_document.confiance_api.json

  # Durée de session courte : un identifiant temporaire compromis expire vite.
  max_session_duration = 3600
}

data "aws_iam_policy_document" "droits_api" {
  statement {
    sid       = "ListerLesZonesAutorisees"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.datalake.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for zone in local.api_lecture : "${zone}/*"]
    }
  }

  statement {
    sid       = "LireLesVariablesExploitables"
    actions   = ["s3:GetObject"]
    resources = [for zone in local.api_lecture : "${aws_s3_bucket.datalake.arn}/${zone}/*"]
  }

  # Écriture SEULE : pas de GetObject, pas de DeleteObject. Un journal d'audit
  # doit pouvoir être écrit, jamais relu ni effacé par celui qui l'écrit —
  # c'est la condition pour qu'il fasse foi (politique P-7, AI Act art. 12).
  statement {
    sid       = "EcrireLeJournalDAudit"
    actions   = ["s3:PutObject"]
    resources = [for zone in local.api_ajout : "${aws_s3_bucket.datalake.arn}/${zone}/*"]
  }
}

resource "aws_iam_role_policy" "droits_api" {
  name   = "${var.nom_projet}-droits-api"
  role   = aws_iam_role.api_scoring.id
  policy = data.aws_iam_policy_document.droits_api.json
}
