"""Garde-fou : le gabarit des variables d'environnement, la pile et le code
disent la même chose.

Pourquoi ce test existe. Trois dérives silencieuses sont possibles entre
`docker/.env.example`, `docker/docker-compose.yml` et le code, et aucune ne
produit d'erreur au démarrage :

1. Une variable référencée par `compose` mais absente du gabarit : l'opérateur
   qui remplit son `.env` ne peut pas la connaître. `compose` substitue alors la
   chaîne vide, et le service démarre mal configuré — mot de passe vide, bucket
   introuvable — sans message clair.
2. Une variable présente au gabarit mais que personne ne consomme : l'opérateur
   renseigne une valeur qui n'a aucun effet. C'est arrivé avec
   `CREDISCORE_MEMOIRE_GO`, documentée comme transmise aux jobs Spark alors que
   `compose` ne la passait pas : les jobs retombaient sur leur valeur par
   défaut, et régler le `.env` ne changeait rien.
3. Une variable inventée par la documentation : `DATALAKE_BUCKET` a figuré dans
   `docs/connexion-aws.md` alors que le code lit `CREDISCORE_BUCKET`. Un `.env`
   rempli d'après la documentation ne configurait donc rien.

Le contrôle est statique : il ne lance ni conteneur ni AWS, et tourne en CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
COMPOSE = RACINE / "docker" / "docker-compose.yml"
GABARIT = RACINE / "docker" / ".env.example"

# Variables fournies par l'hôte ou par AWS, jamais par le fichier .env : elles
# viennent du rôle IAM de l'instance ou de l'environnement de l'opérateur.
HORS_GABARIT = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_PROFILE",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    }
)

SUBSTITUTION = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
AFFECTATION = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")
LECTURE_PYTHON = re.compile(r"""environ(?:\.get)?[(\[]\s*["']([A-Z_][A-Z0-9_]*)["']""")

# Une clé dont le nom contient l'un de ces fragments porte un secret : sa valeur
# d'exemple doit rester un marqueur à remplir. Le contrôle porte sur le NOM et
# non sur la valeur : une liste blanche de valeurs acceptées devrait être
# complétée à chaque nouveau réglage, et se périmerait en silence.
FRAGMENTS_SENSIBLES = ("MOT_DE_PASSE", "PASSWORD", "SECRET", "TOKEN", "SEL_", "_KEY", "CLE_")

# Ce par quoi la valeur d'exemple d'une clé sensible doit commencer. La chaîne
# vide n'y figure pas : `startswith("")` vaut toujours vrai et le contrôle ne
# rejetterait plus rien. Une valeur vide est acceptée par le test lui-même.
MARQUEURS = ("a-remplir", "a remplir", "à-remplir", "à remplir")


def est_sensible(cle: str) -> bool:
    return any(fragment in cle for fragment in FRAGMENTS_SENSIBLES)


def variables_de_compose() -> set[str]:
    """Les `${VAR}` que docker-compose substitue au démarrage."""
    texte = COMPOSE.read_text(encoding="utf-8")
    return set(SUBSTITUTION.findall(texte)) - HORS_GABARIT


def affectations_du_gabarit() -> list[tuple[str, str]]:
    """Les couples (clé, valeur d'exemple) déclarés au gabarit, dans l'ordre."""
    trouvees: list[tuple[str, str]] = []
    for ligne in GABARIT.read_text(encoding="utf-8").splitlines():
        affectation = AFFECTATION.match(ligne.strip())
        if affectation:
            trouvees.append((affectation.group(1), affectation.group(2).strip()))
    return trouvees


def variables_du_gabarit() -> set[str]:
    """Les clés que l'opérateur est invité à renseigner dans son .env."""
    return {cle for cle, _ in affectations_du_gabarit()}


def variables_lues_par_le_code() -> set[str]:
    """Les variables d'environnement lues par le code Python du dépôt."""
    trouvees = set()
    for fichier in sorted(RACINE.glob("pipelines/**/*.py")):
        trouvees |= set(LECTURE_PYTHON.findall(fichier.read_text(encoding="utf-8")))
    return trouvees


def test_le_parcours_trouve_bien_des_variables() -> None:
    """Sans cette vérification, les tests suivants passeraient à vide."""
    assert variables_de_compose(), f"Aucune substitution ${{VAR}} lue dans {COMPOSE.name}."
    assert variables_du_gabarit(), f"Aucune clé lue dans {GABARIT.name}."
    assert variables_lues_par_le_code(), "Aucune lecture d'environnement trouvée dans pipelines/."


@pytest.mark.parametrize("variable", sorted(variables_de_compose()))
def test_variable_de_la_pile_est_documentee_au_gabarit(variable: str) -> None:
    """Toute variable que la pile attend doit figurer au gabarit."""
    assert variable in variables_du_gabarit(), (
        f"{variable} est substituée dans docker-compose.yml mais absente de "
        f".env.example. Un opérateur ne peut pas la deviner : compose "
        f"remplacerait la valeur par une chaîne vide, sans erreur au démarrage."
    )


@pytest.mark.parametrize("variable", sorted(variables_du_gabarit()))
def test_variable_du_gabarit_est_reellement_consommee(variable: str) -> None:
    """Toute variable proposée à l'opérateur doit avoir un effet réel."""
    consommateurs = variables_de_compose() | variables_lues_par_le_code()
    assert variable in consommateurs, (
        f"{variable} est proposée dans .env.example mais n'est consommée ni par "
        f"docker-compose.yml, ni par le code de pipelines/. La renseigner "
        f"n'aurait aucun effet : soit la transmettre, soit la retirer du gabarit."
    )


@pytest.mark.parametrize(
    ("cle", "valeur"), [couple for couple in affectations_du_gabarit() if est_sensible(couple[0])]
)
def test_aucun_secret_en_clair_au_gabarit(cle: str, valeur: str) -> None:
    """Le gabarit est versionné : une clé sensible n'y porte jamais de vraie valeur.

    Un secret poussé sur GitHub y reste : le retirer d'un commit ultérieur ne
    l'efface pas de l'historique, et il doit alors être considéré comme public
    et changé partout.
    """
    assert valeur == "" or valeur.lower().startswith(MARQUEURS), (
        f"{cle} porte une valeur d'exemple qui n'est pas un marqueur à remplir. "
        f"Les secrets réels vont dans docker/.env, jamais dans .env.example, qui "
        f"est versionné. Valeurs acceptées ici : rien, ou un texte commençant "
        f"par « a-remplir »."
    )
