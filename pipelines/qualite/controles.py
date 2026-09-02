"""Controles qualite bloquants sur les sources (controle C-6).

POURQUOI CE FICHIER EXISTE SEPAREMENT DU DAG.

Ces trois controles vivaient dans le corps des taches Airflow. Ils
fonctionnaient, mais ils etaient INTESTABLES : pour verifier qu'un fichier
tronque bloque bien la publication, il fallait tronquer un fichier de 375 Mio
sur S3, lancer le DAG, et regarder. Autrement dit, on ne le verifiait jamais.

Un controle bloquant qu'on ne peut pas exercer est une promesse, pas une
garantie. Ces fonctions ne connaissent ni S3 ni Airflow : elles prennent un
inventaire et levent une exception. Le DAG les appelle, les tests aussi.

CE QU'ILS VERIFIENT, ET DANS QUEL ORDRE.

  1. PRESENCE     les huit fichiers attendus sont-ils la ?
  2. VOLUMETRIE   ont-ils une taille plausible ?
  3. FRAICHEUR    le flux du bureau externe est-il assez recent ?

L'ordre compte : inutile de mesurer la taille d'un fichier absent.

POURQUOI LA TAILLE ET NON LE NOMBRE DE LIGNES.

Compter les lignes demanderait de lire 2,5 Gio pour un controle preliminaire.
La taille en octets detecte les deux pannes reelles — la troncature et l'export
partiel — pour le prix d'un appel a l'API S3.
"""

from __future__ import annotations

from datetime import datetime

# Les huit fichiers attendus, avec leur taille en octets mesuree le 30/07/2026.
# La zone raw/ contient aussi le dictionnaire des colonnes : on nomme donc les
# huit attendus plutot que de compter les objets presents.
TAILLES_ATTENDUES = {
    "application_train.csv": 166_133_370,
    "application_test.csv": 26_567_651,
    "bureau.csv": 170_016_717,
    "bureau_balance.csv": 375_592_889,
    "previous_application.csv": 404_973_293,
    "POS_CASH_balance.csv": 392_703_158,
    "credit_card_balance.csv": 424_582_605,
    "installments_payments.csv": 723_118_349,
}

# Un flux quotidien varie legerement d'un jour a l'autre. Au-dela de 20 %, ce
# n'est plus une fluctuation : c'est une troncature, un export partiel, ou un
# changement de format en amont.
TOLERANCE_TAILLE = 0.20


class SourceInvalide(Exception):
    """Une source ne permet pas de publier. La suite du pipeline est bloquee.

    Une exception dediee plutot que ValueError : dans le journal d'Airflow, le
    nom du type dit immediatement s'il s'agit d'un probleme de donnees ou d'une
    panne technique.
    """


def controler_presence(inventaire, attendus=None):
    """Les fichiers attendus sont-ils tous la ?"""
    attendus = attendus or list(TAILLES_ATTENDUES)
    manquants = [f for f in attendus if f not in inventaire]
    if manquants:
        raise SourceInvalide(
            f"Fichiers absents de raw/ : {manquants}. "
            f"Le traitement ne peut pas demarrer."
        )
    return f"Les {len(attendus)} fichiers attendus sont presents."


def controler_volumetrie(inventaire, references=None, tolerance=TOLERANCE_TAILLE):
    """Les tailles sont-elles plausibles ?

    On signale TOUTES les anomalies d'un coup, pas seulement la premiere : si
    trois fichiers ont ete tronques par le meme incident, il faut le voir sur
    la meme execution.
    """
    references = references or TAILLES_ATTENDUES
    anomalies = []
    for fichier, attendu in references.items():
        if fichier not in inventaire:
            continue
        constate = inventaire[fichier]["taille"]
        ecart = abs(constate - attendu) / attendu
        if ecart > tolerance:
            anomalies.append(
                f"{fichier} : {constate:,} octets contre {attendu:,} attendus "
                f"({ecart * 100:.1f} % d'ecart)".replace(",", " ")
            )

    if anomalies:
        raise SourceInvalide(
            "Volumetrie anormale, publication bloquee :\n  " + "\n  ".join(anomalies)
        )
    return f"Les {len(references)} fichiers ont une taille conforme."


def controler_fraicheur(inventaire, fichier="bureau.csv", maximum_jours=365, maintenant=None):
    """Le flux du bureau externe est-il assez recent ?

    `maintenant` est un parametre pour que le test puisse se placer dans le
    futur sans attendre. Un controle de fraicheur qu'on ne peut tester qu'en
    attendant un an ne se teste pas.
    """
    if fichier not in inventaire:
        raise SourceInvalide(f"{fichier} absent : fraicheur non verifiable.")

    modifie = datetime.fromisoformat(inventaire[fichier]["modifie"])
    maintenant = maintenant or datetime.now(modifie.tzinfo)
    age = (maintenant - modifie).days

    if age > maximum_jours:
        raise SourceInvalide(
            f"Flux {fichier} vieux de {age} jours, au-dela des "
            f"{maximum_jours} tolerees. Un historique perime ne reflete plus "
            f"la situation du demandeur."
        )
    return f"Flux {fichier} depose il y a {age} jours."
