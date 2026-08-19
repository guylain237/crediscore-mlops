###############################################################################
# compute.tf — la VM de traitement, seule ressource facturée à l'heure
#
# C'est le démonstrateur mono-VM : Airflow, Spark local, PostgreSQL, MLflow,
# Prometheus, Grafana et l'API y tournent en conteneurs.
#
# ══════════════════════════════════════════════════════════════════════════
#  LE MÉCANISME D'EXTINCTION — politique P-9, née des 50 USD de la RDS oubliée
# ══════════════════════════════════════════════════════════════════════════
#
#   Le soir :   terraform apply -var="vm_active=false"    -> 1 to destroy
#   Le matin :  terraform apply                            -> 1 to add
#
# On utilise `count` plutôt que `terraform destroy -target` : le cycle reste un
# plan/apply normal, relu avant exécution. Tout le reste — VPC, IAM, S3 — est
# gratuit et n'est jamais détruit.
###############################################################################

# L'identifiant d'une AMI change à chaque mise à jour et diffère selon la
# région. On le lit dans le paramètre public publié par AWS plutôt que de
# l'écrire en dur : le code reste valable partout, et la machine part toujours
# d'un système à jour.
data "aws_ssm_parameter" "ami_al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# Ta clé publique est déposée sur la VM ; ta clé privée ne quitte jamais ton
# poste. AWS ne voit que la moitié publique.
resource "aws_key_pair" "admin" {
  key_name   = "${var.nom_projet}-admin"
  public_key = file(pathexpand(var.chemin_cle_publique))

  tags = {
    Name = "${var.nom_projet}-admin"
  }
}

resource "aws_instance" "traitement" {
  # 1 quand vm_active = true, 0 sinon. C'est l'interrupteur du soir.
  count = var.vm_active ? 1 : 0

  ami           = data.aws_ssm_parameter.ami_al2023.value
  instance_type = var.type_instance

  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.vm_traitement.id]
  iam_instance_profile   = aws_iam_instance_profile.vm_traitement.name
  key_name               = aws_key_pair.admin.key_name

  root_block_device {
    volume_size = var.taille_disque_go
    volume_type = "gp3"

    # Chiffrement au repos (politique P-8, contrôle C-8). La clé gérée par AWS
    # est gratuite ; une clé KMS dédiée coûterait ~1 USD/mois pour une
    # gouvernance de clé sans objet sur un disque éphémère détruit chaque soir.
    encrypted = true

    delete_on_termination = true
  }

  # IMDSv2 obligatoire. Sans cela, une faille de type SSRF dans une application
  # web de la VM permettrait de lire les identifiants du rôle IAM par une simple
  # requête HTTP. `http_tokens = "required"` impose un jeton signé et ferme
  # cette porte. Détail technique, conséquence majeure.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # Script exécuté une seule fois, au tout premier démarrage : installation de
  # Docker et des outils. Modifier ce fichier force le remplacement de la VM.
  user_data = file("${path.module}/scripts/init-vm.sh")

  tags = {
    Name = "${var.nom_projet}-vm-traitement"
    Role = "traitement"
  }
}
