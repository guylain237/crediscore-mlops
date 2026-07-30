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
