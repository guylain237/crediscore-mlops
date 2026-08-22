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
  # L'instance existe en permanence. L'interrupteur du soir l'ARRÊTE, il ne la
  # détruit plus (voir aws_ec2_instance_state, plus bas, et la décision D-108).

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
    # suffit ici : le disque ne contient aucune donnée qui n'existe déjà,
    # chiffrée, dans le data lake.
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


# =============================================================================
# L'INTERRUPTEUR DU SOIR — arrêt, et non destruction
# =============================================================================
#
# Une instance ARRÊTÉE ne facture plus aucune heure de calcul : il ne reste que
# le disque, de l'ordre de quelques centimes par jour. Une instance DÉTRUITE ne
# coûte rien du tout, mais emporte son disque racine — donc PostgreSQL, le
# schéma en étoile, le feature store, la piste d'audit des décisions et les
# images Docker construites, dont celle d'Airflow qui demande une dizaine de
# minutes à reconstruire.
#
# L'arbitrage est sans ambiguïté à ce stade du projet : quelques centimes par
# nuit contre une demi-heure de remise en route chaque matin, sur une machine
# qui porte désormais de l'état.
#
# La reproductibilité complète reste démontrable — et sera filmée une fois pour
# la vidéo du Bloc 2 : `terraform destroy` puis `terraform apply` reconstruit
# l'ensemble à partir du seul code.
#
#   Le soir  :  terraform apply -var="vm_active=false"     -> stopped
#   Le matin :  terraform apply                            -> running
#
# Attention : l'adresse IP publique change à chaque redémarrage. Relire
# `terraform output` le matin plutôt que de garder l'ancienne en favori.
resource "aws_ec2_instance_state" "traitement" {
  instance_id = aws_instance.traitement.id
  state       = var.vm_active ? "running" : "stopped"
}
