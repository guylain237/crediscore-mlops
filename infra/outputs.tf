###############################################################################
# outputs.tf — ce que Terraform rend à l'extérieur
#
# Les sorties sont l'interface publique de ce module : elles évitent de
# recopier à la main des noms générés par le code, donc les fautes de frappe.
#
# Lecture pour un humain :        terraform output
# Lecture pour un script :        terraform output -raw datalake_bucket
###############################################################################

output "datalake_bucket" {
  description = "Nom du bucket du data lake. À reporter dans .env sous DATALAKE_BUCKET."
  value       = aws_s3_bucket.datalake.id
}

output "datalake_zones" {
  description = "URI des trois zones — utiles pour les DAGs Airflow et les jobs Spark."
  value       = [for zone in local.zones : "s3://${aws_s3_bucket.datalake.id}/${zone}"]
}

output "region" {
  description = "Région de déploiement, à réutiliser telle quelle côté application."
  value       = var.region
}

output "commande_depot_csv" {
  description = "Commande prête à copier pour déposer les sources en zone brute."
  value       = "aws s3 sync <dossier_input> s3://${aws_s3_bucket.datalake.id}/raw/ --exclude \"*\" --include \"*.csv\""
}
