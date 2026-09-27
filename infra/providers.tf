###############################################################################
# providers.tf — de quoi Terraform a besoin pour démarrer
#
# Terraform, seul, ne connaît aucun cloud. Toute sa connaissance d'AWS vient
# d'un plugin — un « provider » — déclaré ici et téléchargé par
# `terraform init`. C'est exactement le rôle que joue requirements.txt pour
# Python : on déclare ce dont on dépend, et une commande l'installe.
###############################################################################

terraform {
  # Version minimale de l'outil lui-même.
  # >= 1.10 est exigé par `use_lockfile` dans backend.tf (verrouillage natif S3,
  # qui remplace l'ancienne table DynamoDB).
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source = "hashicorp/aws"

      # `~> 6.0` autorise 6.1, 6.2… mais jamais 7.0 : on accepte les
      # corrections, on refuse les changements de rupture. Le passage à une
      # version majeure doit être un acte volontaire, jamais une surprise
      # d'un lundi matin.
      #
      # Les versions exactes réellement utilisées sont figées par Terraform
      # dans .terraform.lock.hcl — fichier à VERSIONNER (voir README).
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Volontairement absent : `profile`.
  #
  # Le provider utilise alors la même chaîne de recherche que boto3 :
  # variable AWS_PROFILE → fichier ~/.aws/config → rôle IAM de la machine.
  # Conséquence pratique : le même code fonctionne sur le poste de
  # développement (où AWS_PROFILE vaut « crediscore ») et dans GitHub Actions
  # (où les identifiants viennent d'un rôle assumé par OIDC, sans profil).
  #
  # Figer `profile = "crediscore"` ici casserait la CI, puisque ce profil
  # n'existe que sur le poste de développement.

  # Étiquettes appliquées automatiquement à TOUTE ressource créée par ce
  # provider. Deux usages concrets :
  #   - Cost Explorer devient lisible : on filtre sur Projet = CrediScore et
  #     on sait ce que coûte ce projet, séparément du reste du compte ;
  #   - une ressource oubliée se retrouve — donc se supprime.
  default_tags {
    tags = {
      Projet        = "crediscore"
      Environnement = var.environnement
      GerePar       = "Terraform"
      Depot         = "crediscore-mlops"
    }
  }
}
