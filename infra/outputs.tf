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

# ─────────────────────────────────────────────────────────────────────────────
# Réseau et VM
# ─────────────────────────────────────────────────────────────────────────────

output "id_vpc" {
  description = "Identifiant du VPC — à montrer dans la vidéo du Bloc 2."
  value       = aws_vpc.principal.id
}

output "vm_active" {
  description = "La VM est-elle allumée ? false = aucune facturation horaire en cours."
  value       = var.vm_active
}

output "ip_publique_vm" {
  description = "Adresse publique de la VM. Change à chaque recréation."
  value       = var.vm_active ? aws_instance.traitement.public_ip : "VM eteinte"
}

output "commande_ssh" {
  description = "Commande de connexion prête à copier."
  value = var.vm_active ? (
    "ssh -i ~/.ssh/id_ed25519 ec2-user@${aws_instance.traitement.public_ip}"
  ) : "VM eteinte — terraform apply pour la rallumer"
}

output "interfaces_web" {
  description = "URL des interfaces, accessibles depuis la seule IP administrateur."
  value = var.vm_active ? {
    for nom, port in local.ports_applicatifs :
    nom => "http://${aws_instance.traitement.public_ip}:${port}"
  } : {}
}

output "role_api_arn" {
  description = "ARN du rôle applicatif au moindre privilège (contrôle C-9)."
  value       = aws_iam_role.api_scoring.arn
}
