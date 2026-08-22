###############################################################################
# network.tf — le réseau privé qui isole l'infrastructure
#
# Un VPC est un réseau virtuel qui n'appartient qu'à toi : rien n'y entre sans
# une règle explicite. Tout ce qui suit est GRATUIT (VPC, sous-réseaux,
# passerelle Internet, tables de routage, groupes de sécurité) — seule la VM du
# fichier compute.tf est facturée à l'heure.
#
# Ce qui coûterait cher et que l'on n'utilise PAS : la NAT Gateway, ~32 €/mois
# facturée à l'heure même inutilisée. Voir le commentaire du sous-réseau privé.
###############################################################################

# Les zones de disponibilité sont des datacenters distincts d'une même région.
# On lit la liste au lieu de l'écrire en dur : le code reste valable si l'on
# change de région (variable `region`).
data "aws_availability_zones" "disponibles" {
  state = "available"
}

# ─────────────────────────────────────────────────────────────────────────────
# Le VPC : la coquille réseau
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_vpc" "principal" {
  cidr_block = var.cidr_vpc # 10.0.0.0/16 = 65 536 adresses privées

  # Nécessaires pour que les machines se résolvent par nom, et pour que les
  # points de terminaison AWS (S3, SSM) fonctionnent depuis la VM.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.nom_projet}-vpc"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Sous-réseau PUBLIC — il héberge la VM de traitement
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.principal.id
  cidr_block        = var.cidr_sous_reseau_public
  availability_zone = data.aws_availability_zones.disponibles.names[0]

  # « Public » ne signifie pas « ouvert » : cela signifie que le sous-réseau a
  # une route vers Internet. Ce qui protège la VM, c'est le groupe de sécurité
  # ci-dessous, qui n'autorise QUE ton adresse IP.
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.nom_projet}-public"
    Type = "public"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Sous-réseau PRIVÉ — vide dans le démonstrateur, prévu pour la cible
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_subnet" "prive" {
  vpc_id            = aws_vpc.principal.id
  cidr_block        = var.cidr_sous_reseau_prive
  availability_zone = data.aws_availability_zones.disponibles.names[0]

  # Aucune route vers Internet : une machine placée ici serait injoignable
  # depuis l'extérieur, et ne pourrait pas non plus sortir sans NAT Gateway.
  #
  # C'est là que vivraient, en architecture cible, la base managée et les
  # nœuds de traitement. Dans le démonstrateur il reste vide : y placer la VM
  # imposerait une NAT Gateway à ~32 €/mois pour zéro capacité démontrée en
  # plus. Le sous-réseau est déclaré parce qu'il documente la cible et ne
  # coûte rien.
  tags = {
    Name = "${var.nom_projet}-prive"
    Type = "prive"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Passerelle Internet et routage
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_internet_gateway" "principal" {
  vpc_id = aws_vpc.principal.id

  tags = {
    Name = "${var.nom_projet}-igw"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.principal.id

  # « Tout ce qui ne concerne pas le VPC part vers Internet ».
  # 0.0.0.0/0 = toutes les destinations.
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.principal.id
  }

  tags = {
    Name = "${var.nom_projet}-rt-public"
  }
}

# Une table de routage ne s'applique qu'aux sous-réseaux qui lui sont associés.
# Le sous-réseau privé n'est associé à rien : il n'a donc aucune route sortante.
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ─────────────────────────────────────────────────────────────────────────────
# Groupe de sécurité — le pare-feu de la VM
# ─────────────────────────────────────────────────────────────────────────────
locals {
  # Les interfaces web de la pile applicative. Elles seront exposées
  # uniquement à ton adresse IP, jamais à Internet.
  ports_applicatifs = {
    airflow    = 8080
    grafana    = 3000
    mlflow     = 5000
    prometheus = 9090
    api        = 8000
  }
}

# ATTENTION : AWS restreint les descriptions de groupes de securite et de regles
# a ce jeu de caracteres, apostrophe et accents EXCLUS :
#   a-zA-Z0-9 . _ - : / ( ) # , @ [ ] + = & ; { } ! $ *
# `terraform validate` ne le detecte pas — l'erreur ne tombe qu'a l'`apply`.
# Les descriptions ci-dessous sont donc volontairement sans accent ni apostrophe.
resource "aws_security_group" "vm_traitement" {
  name        = "${var.nom_projet}-vm-traitement"
  description = "Acces restreint a la seule IP administrateur"
  vpc_id      = aws_vpc.principal.id

  tags = {
    Name = "${var.nom_projet}-sg-vm"
  }
}

# Règles écrites une par une plutôt qu'en blocs `ingress` internes : chaque
# règle devient une ressource nommée, visible individuellement dans le `plan`
# et dans l'état. On voit donc exactement ce qui s'ouvre et ce qui se ferme.

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.vm_traitement.id
  description       = "SSH depuis le poste administrateur"

  cidr_ipv4   = "${var.ip_admin}/32" # /32 = cette adresse EXACTEMENT
  ip_protocol = "tcp"
  from_port   = 22
  to_port     = 22
}

resource "aws_vpc_security_group_ingress_rule" "applicatifs" {
  for_each = local.ports_applicatifs

  security_group_id = aws_security_group.vm_traitement.id
  description       = "Interface ${each.key} depuis le poste administrateur"

  cidr_ipv4   = "${var.ip_admin}/32"
  ip_protocol = "tcp"
  from_port   = each.value
  to_port     = each.value
}

# Sortie : autorisée vers tout. La VM doit pouvoir télécharger les images
# Docker, joindre S3 et les points de terminaison AWS. Restreindre la sortie
# imposerait des VPC endpoints — utile en production, hors sujet ici.
resource "aws_vpc_security_group_egress_rule" "sortie" {
  security_group_id = aws_security_group.vm_traitement.id
  description       = "Sortie Internet (images Docker, S3, SSM)"

  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "-1" # -1 = tous protocoles, tous ports
}
