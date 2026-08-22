"""Garde-fou : les descriptions de groupes de sécurité respectent le jeu de
caractères imposé par AWS.

Pourquoi ce test existe (incident du 19/08/2026) : `terraform validate` déclare
la configuration valide, puis `terraform apply` échoue au bout de plusieurs
minutes — après avoir déjà créé une douzaine de ressources — parce qu'une
description contenait une apostrophe. AWS n'accepte, pour ces champs, que :

    a-zA-Z0-9 . _ - : / ( ) # , @ [ ] + = & ; { } ! $ *

Ni accent, ni apostrophe. Le contrôle coûte quelques millisecondes ici et
évite un `apply` interrompu à mi-parcours, qui laisse l'infrastructure dans un
état partiel.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[1] / "infra"

CARACTERES_AUTORISES = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " ._-:/()#,@[]+=&;{}!$*"
)

# Seuls ces types de ressources sont soumis à la restriction.
TYPES_CONCERNES = ("aws_security_group", "aws_vpc_security_group_")

LIGNE_RESOURCE = re.compile(r'^\s*resource\s+"([^"]+)"')
LIGNE_DESCRIPTION = re.compile(r'^\s*description\s*=\s*"(.*)"\s*$')


def descriptions_de_groupes_de_securite() -> list[tuple[str, int, str]]:
    """Retourne (fichier, ligne, description) pour les ressources concernées."""
    trouvees: list[tuple[str, int, str]] = []
    for fichier in sorted(INFRA.glob("*.tf")):
        type_courant = ""
        for numero, ligne in enumerate(
            fichier.read_text(encoding="utf-8").splitlines(), start=1
        ):
            entete = LIGNE_RESOURCE.match(ligne)
            if entete:
                type_courant = entete.group(1)
                continue
            description = LIGNE_DESCRIPTION.match(ligne)
            if description and type_courant.startswith(TYPES_CONCERNES):
                trouvees.append((fichier.name, numero, description.group(1)))
    return trouvees


def test_des_descriptions_sont_bien_trouvees() -> None:
    """Si le parcours ne trouve rien, le test suivant passerait à vide."""
    assert descriptions_de_groupes_de_securite(), (
        "Aucune description de groupe de sécurité trouvée dans infra/ : "
        "le garde-fou ne contrôlerait plus rien."
    )


@pytest.mark.parametrize(
    ("fichier", "numero", "description"), descriptions_de_groupes_de_securite()
)
def test_description_acceptee_par_aws(fichier: str, numero: int, description: str) -> None:
    refuses = sorted({c for c in description if c not in CARACTERES_AUTORISES})
    assert not refuses, (
        f"{fichier}:{numero} — caractères refusés par AWS {refuses} dans "
        f'"{description}". Retirer accents et apostrophes : l\'apply échouerait '
        f"après avoir créé une partie des ressources."
    )
