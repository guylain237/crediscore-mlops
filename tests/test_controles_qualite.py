"""Exerce les controles bloquants sur les sources (controle C-6).

CE QUE CES TESTS PROUVENT.

Que le pipeline s'arrete vraiment. Jusqu'ici, la seule facon de le verifier
etait de tronquer un fichier de 375 Mio sur S3 et de lancer le DAG — donc on
ne le verifiait pas.

Chaque test decrit une panne reelle et exige le blocage :

  un fichier n'est pas arrive
  un export s'est arrete a mi-chemin
  un flux exterieur a cesse d'etre alimente

Un controle qu'on n'exerce jamais n'est pas un controle, c'est une intention.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "pipelines"))

from qualite.controles import (
    TAILLES_ATTENDUES,
    SourceInvalide,
    controler_fraicheur,
    controler_presence,
    controler_volumetrie,
)

DEPOT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def inventaire_sain():
    """Ce que S3 renvoie un jour ou tout va bien."""
    return {
        fichier: {"taille": taille, "modifie": DEPOT.isoformat()}
        for fichier, taille in TAILLES_ATTENDUES.items()
    }


# --- Le cas normal -----------------------------------------------------------


def test_un_lot_sain_passe_les_trois_controles():
    inventaire = inventaire_sain()
    assert "8 fichiers" in controler_presence(inventaire)
    assert "conforme" in controler_volumetrie(inventaire)
    assert "1 jours" in controler_fraicheur(
        inventaire, maintenant=DEPOT + timedelta(days=1)
    )


# --- Presence ----------------------------------------------------------------


def test_un_fichier_absent_bloque():
    inventaire = inventaire_sain()
    del inventaire["bureau.csv"]

    with pytest.raises(SourceInvalide) as erreur:
        controler_presence(inventaire)
    assert "bureau.csv" in str(erreur.value)


def test_le_message_nomme_tous_les_absents():
    """Trois fichiers manquants doivent apparaitre sur la meme execution.

    Sinon on corrige, on relance, on decouvre le suivant : trois cycles pour
    un seul incident.
    """
    inventaire = inventaire_sain()
    for fichier in ("bureau.csv", "application_test.csv", "credit_card_balance.csv"):
        del inventaire[fichier]

    with pytest.raises(SourceInvalide) as erreur:
        controler_presence(inventaire)
    for fichier in ("bureau.csv", "application_test.csv", "credit_card_balance.csv"):
        assert fichier in str(erreur.value)


# --- Volumetrie --------------------------------------------------------------


def test_un_fichier_tronque_bloque():
    """La panne la plus frequente : un export interrompu."""
    inventaire = inventaire_sain()
    inventaire["installments_payments.csv"]["taille"] = 12_000_000  # 1,7 % du poids

    with pytest.raises(SourceInvalide) as erreur:
        controler_volumetrie(inventaire)
    assert "installments_payments.csv" in str(erreur.value)
    assert "98" in str(erreur.value)  # 98,3 % d'ecart


def test_un_fichier_anormalement_gros_bloque_aussi():
    """Un doublement de taille est aussi anormal qu'une troncature.

    Il signale generalement un fichier concatene deux fois — et le pipeline
    compterait alors chaque dossier en double.
    """
    inventaire = inventaire_sain()
    inventaire["bureau.csv"]["taille"] = TAILLES_ATTENDUES["bureau.csv"] * 2

    with pytest.raises(SourceInvalide):
        controler_volumetrie(inventaire)


def test_une_variation_normale_passe():
    """Un flux quotidien bouge un peu. Le controle ne doit pas crier pour 5 %."""
    inventaire = inventaire_sain()
    for fichier in inventaire:
        inventaire[fichier]["taille"] = int(TAILLES_ATTENDUES[fichier] * 1.05)

    assert "conforme" in controler_volumetrie(inventaire)


def test_la_tolerance_est_bien_a_vingt_pour_cent():
    """Le seuil doit etre exerce des deux cotes, sinon il n'est pas mesure."""
    inventaire = inventaire_sain()
    reference = TAILLES_ATTENDUES["bureau.csv"]

    inventaire["bureau.csv"]["taille"] = int(reference * 0.81)
    controler_volumetrie(inventaire)  # juste sous le seuil : passe

    inventaire["bureau.csv"]["taille"] = int(reference * 0.79)
    with pytest.raises(SourceInvalide):
        controler_volumetrie(inventaire)


# --- Fraicheur ---------------------------------------------------------------


def test_un_flux_perime_bloque():
    inventaire = inventaire_sain()

    with pytest.raises(SourceInvalide) as erreur:
        controler_fraicheur(
            inventaire, maximum_jours=2, maintenant=DEPOT + timedelta(days=10)
        )
    assert "10 jours" in str(erreur.value)


def test_un_flux_recent_passe():
    inventaire = inventaire_sain()
    message = controler_fraicheur(
        inventaire, maximum_jours=2, maintenant=DEPOT + timedelta(hours=30)
    )
    assert "1 jours" in message


def test_la_fraicheur_d_un_fichier_absent_ne_passe_pas_en_silence():
    """Un fichier absent ne doit pas rendre le controle inoperant.

    C'est le piege classique : la fonction ne trouve rien a verifier, ne leve
    rien, et le pipeline continue en croyant le controle passe.
    """
    with pytest.raises(SourceInvalide):
        controler_fraicheur({}, maximum_jours=2)
