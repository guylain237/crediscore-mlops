###############################################################################
# variables.tf — les points de réglage de l'infrastructure
#
# C'est ce fichier qui rend vraie la décision D-101 : « passer du démonstrateur
# à la cible est un changement de variables, pas de code ». Tout ce qui est
# susceptible de changer d'un environnement à l'autre est déclaré ici, et
# nulle part ailleurs.
###############################################################################

variable "region" {
  description = "Région AWS. Doit rester identique partout : profil CLI, backend, ressources."
  type        = string
  default     = "eu-north-1"

  validation {
    # Garde-fou RGPD, pas une coquetterie : les données de crédit sont des
    # données personnelles. Une région hors UE impliquerait de justifier un
    # transfert international. Le code refuse donc l'erreur au lieu de la
    # laisser passer silencieusement.
    condition     = startswith(var.region, "eu-")
    error_message = "Les données étant personnelles, la région doit être européenne (eu-*)."
  }
}

variable "nom_projet" {
  description = "Préfixe de nommage de toutes les ressources."
  type        = string
  default     = "crediscore"
}

variable "environnement" {
  description = "Dimensionnement visé : demonstrateur (ressources minimales) ou cible."
  type        = string
  default     = "demonstrateur"

  validation {
    condition     = contains(["demonstrateur", "cible"], var.environnement)
    error_message = "Valeurs acceptées : demonstrateur, cible."
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Réseau
# ─────────────────────────────────────────────────────────────────────────────

variable "cidr_vpc" {
  description = "Plage d'adresses privées du VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "cidr_sous_reseau_public" {
  description = "Sous-réseau public — héberge la VM de traitement."
  type        = string
  default     = "10.0.1.0/24"
}

variable "cidr_sous_reseau_prive" {
  description = "Sous-réseau privé — vide dans le démonstrateur, prévu pour la cible."
  type        = string
  default     = "10.0.2.0/24"
}

variable "ip_admin" {
  description = <<-DESC
    Adresse IP publique du poste administrateur. C'est la SEULE adresse
    autorisée à joindre la VM (SSH et interfaces web).

    La retrouver :  (Invoke-RestMethod https://checkip.amazonaws.com).Trim()

    Une IP résidentielle change : si l'accès est refusé un matin, c'est
    probablement elle. Mettre à jour terraform.tfvars puis `terraform apply`.
  DESC
  type        = string

  validation {
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.ip_admin))
    error_message = "Format attendu : une adresse IPv4, sans masque (ex. 203.0.113.10)."
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# VM de traitement
# ─────────────────────────────────────────────────────────────────────────────

variable "vm_active" {
  description = <<-DESC
    Interrupteur de la VM — la seule ressource facturée à l'heure.

      Le soir :  terraform apply -var="vm_active=false"   -> instance arrêtée
      Le matin : terraform apply                          -> instance démarrée

    ARRÊT, et non destruction (décision D-108) : le disque porte PostgreSQL,
    donc le schéma en étoile, le feature store et la piste d'audit. Une
    instance arrêtée ne facture plus aucune heure de calcul ; seul le disque
    reste, pour quelques centimes par jour.

    L'adresse IP publique change à chaque redémarrage : relire
    `terraform output` le matin.

    Politique P-9 du plan de gouvernance. Le reste (VPC, IAM, S3) n'est jamais
    détruit.
  DESC
  type        = bool
  default     = true
}

variable "type_instance" {
  description = <<-DESC
    Gabarit de la VM. La pile complète (Airflow + PostgreSQL + MLflow +
    Prometheus + Grafana + Spark local) demande environ 8 Go de mémoire.

      t3.medium : 2 vCPU, 4 Go  — ~0,05 USD/h — trop juste, la machine
                  commencera à utiliser le swap
      t3.large  : 2 vCPU, 8 Go  — ~0,09 USD/h — retenu

    À ~9 h de fonctionnement par jour et avec extinction le soir, t3.large
    revient à environ 0,80 USD/jour. Laissée allumée jour et nuit, elle
    coûterait 2,10 USD/jour : l'extinction n'est pas une coquetterie.
  DESC
  type        = string
  default     = "t3.large"
}

variable "taille_disque_go" {
  description = "Disque racine en Go. Les images Docker de la pile en occupent ~15."
  type        = number
  default     = 30

  validation {
    condition     = var.taille_disque_go >= 20
    error_message = "20 Go minimum : en dessous, les images Docker ne tiennent pas."
  }
}

variable "chemin_cle_publique" {
  description = "Clé publique SSH déposée sur la VM. La clé privée ne quitte jamais le poste."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "jours_conservation_versions" {
  description = <<-DESC
    Durée de conservation des anciennes versions d'objets S3.

    Le versionnage protège d'un écrasement accidentel, mais chaque version
    occupe — et facture — son propre stockage. Sans purge, un `sync` relancé
    plusieurs fois sur 2,5 Go de CSV multiplie la facture sans rien apporter.
  DESC
  type        = number
  default     = 30

  validation {
    condition     = var.jours_conservation_versions >= 1
    error_message = "Il faut au moins 1 jour de rétention."
  }
}
