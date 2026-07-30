###############################################################################
# backend.tf — où Terraform range sa mémoire
#
# Terraform tient un inventaire de ce qu'il a créé : le « state ». Sans lui, il
# serait incapable de distinguer « ce bucket n'existe pas, je le crée » de
# « ce bucket est celui que j'ai créé la semaine dernière, je n'y touche pas ».
#
# Cet inventaire ne doit PAS rester sur un disque local :
#   - il contient des valeurs sensibles (donc jamais dans git) ;
#   - il est le seul moyen fiable de savoir quoi détruire — donc de maîtriser
#     la facture. Le perdre, c'est perdre le contrôle du compte ;
#   - la CI devra le lire pour déployer.
#
# Il vit donc dans le bucket d'amorçage, créé à la main une seule fois
# (voir docs/connexion-aws.md, étape 6). Ce bucket-là ne peut pas être créé
# par ce code : il doit exister AVANT le premier `terraform init`.
###############################################################################

terraform {
  backend "s3" {
    # ── Configuration PARTIELLE, volontairement ──────────────────────────────
    # Le bloc backend est lu avant l'évaluation des variables : il n'accepte
    # ni var.*, ni local.*, ni interpolation. Tout doit être littéral.
    #
    # Or le nom du bucket d'état contient le numéro de compte AWS, qui n'a pas
    # à figurer dans un dépôt public. La parade prévue par Terraform est la
    # configuration partielle : on omet ici les valeurs à protéger, et on les
    # fournit à l'initialisation depuis un fichier non versionné.
    #
    #     terraform init -backend-config=backend.hcl
    #
    # `bucket` est donc absent de ce fichier — il est dans backend.hcl.
    # Voir backend.hcl.example pour le gabarit.

    key = "infra/terraform.tfstate" # chemin de l'état DANS le bucket

    region = "eu-north-1" # non sensible, et le backend ne lit pas var.region

    encrypt = true # chiffrement au repos de l'état lui-même

    # Verrou d'écriture : empêche deux `apply` simultanés de se marcher dessus
    # et de corrompre l'état. Depuis Terraform 1.10, S3 le fait nativement
    # (écritures conditionnelles) — plus besoin de table DynamoDB.
    use_lockfile = true

    # `profile` volontairement absent : comme le provider, le backend suit la
    # chaîne de credentials (AWS_PROFILE en local, OIDC en CI).
  }
}
