"""Consolide les versements de installments_payments en echeances.

Le fichier source a une ligne par VERSEMENT. Or une meme echeance peut etre
reglee en plusieurs fois : 640 905 echeances sont dans ce cas. Si on agrege
sans les regrouper d'abord, un client qui paie tout en deux fois parait ne
payer que la moitie, et son retard change de signe.

Ce job produit donc une ligne par ECHEANCE, avec le retard reel et le taux de
paiement reel.

Entree : raw/installments_payments.csv   -> 13 605 401 lignes
Sortie : clean/installments_consolide    -> 12 951 918 lignes attendues
"""

import os
import sys

from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commun

# Les trois colonnes qui identifient une echeance de facon unique.
CLES_ECHEANCE = ["SK_ID_PREV", "NUM_INSTALMENT_VERSION", "NUM_INSTALMENT_NUMBER"]

# Nombre d'echeances mesure en pandas sur le jeu complet (voir le notebook
# 02_analyse_crediscore et docs/bornes_qualite.md du depot crediscore-ml).
# Le job doit retrouver exactement ce nombre.
ECHEANCES_ATTENDUES = 12_951_918


def consolider(donnees):
    """Regroupe les versements d'une meme echeance en une seule ligne."""

    # Trois regles, une par colonne :
    #
    #   AMT_PAYMENT        -> somme. Le client a paye le total, pas un morceau.
    #   DAYS_ENTRY_PAYMENT -> maximum. L'echeance est soldee au dernier
    #                         versement. Les jours sont negatifs, donc le
    #                         maximum est bien la date la plus recente.
    #   AMT_INSTALMENT     -> premier. Le montant du est identique sur toutes
    #   DAYS_INSTALMENT       les lignes d'une meme echeance (verifie a 100 %).
    #
    # SK_ID_CURR est aussi identique : c'est le dossier auquel le credit
    # appartient. On le garde pour pouvoir agreger par dossier ensuite.
    echeances = donnees.groupBy(CLES_ECHEANCE).agg(
        F.first("SK_ID_CURR").alias("SK_ID_CURR"),
        F.first("AMT_INSTALMENT").alias("MONTANT_DU"),
        F.first("DAYS_INSTALMENT").alias("JOUR_ECHEANCE"),
        F.sum("AMT_PAYMENT").alias("MONTANT_PAYE"),
        F.max("DAYS_ENTRY_PAYMENT").alias("JOUR_PAIEMENT"),
        F.count("*").alias("NB_VERSEMENTS"),
    )

    # Retard en jours. Positif = paye en retard, negatif = paye en avance.
    # Reste a NULL si l'echeance n'a jamais ete payee : on ne sait pas.
    echeances = echeances.withColumn(
        "RETARD_JOURS", F.col("JOUR_PAIEMENT") - F.col("JOUR_ECHEANCE")
    )

    # Taux de paiement. Vaut 1 quand l'echeance est soldee, moins si le client
    # n'a paye qu'une partie.
    #
    # 290 lignes ont un montant du a zero. On ne divise donc pas aveuglement :
    # sans ce garde-fou, Spark produirait des valeurs infinies qui entreraient
    # sans bruit dans le feature store.
    echeances = echeances.withColumn(
        "TAUX_PAIEMENT",
        F.when(F.col("MONTANT_DU") > 0, F.col("MONTANT_PAYE") / F.col("MONTANT_DU")),
    )

    # Une echeance sans aucun paiement enregistre. On garde l'information dans
    # une colonne plutot que de remplir le retard par zero : ne pas avoir paye
    # n'est pas la meme chose que d'avoir paye a l'heure.
    echeances = echeances.withColumn(
        "JAMAIS_PAYEE", F.col("MONTANT_PAYE").isNull()
    )

    return echeances


def main():
    echantillon = int(os.environ.get("CREDISCORE_ECHANTILLON", "0"))
    memoire = int(os.environ.get("CREDISCORE_MEMOIRE_GO", "4"))

    spark = commun.creer_session("consolider-installments", memoire_go=memoire)

    print("Lecture de installments_payments.csv")
    versements = commun.lire_source(
        spark, "installments_payments.csv", echantillon=echantillon
    )
    nb_versements = versements.count()
    print(f"  {nb_versements:,} versements lus".replace(",", " "))

    echeances = consolider(versements)
    echeances.cache()
    nb_echeances = echeances.count()
    print(f"  {nb_echeances:,} echeances apres consolidation".replace(",", " "))
    print(f"  {nb_versements - nb_echeances:,} lignes regroupees".replace(",", " "))

    # Verification. Sur le jeu complet, le nombre d'echeances doit correspondre
    # a celui mesure en pandas. C'est le test le plus simple qui soit, et le
    # plus utile : si Spark et pandas ne comptent pas pareil, l'un des deux se
    # trompe, et il faut le savoir avant d'aller plus loin.
    if echantillon == 0:
        if nb_echeances != ECHEANCES_ATTENDUES:
            raise SystemExit(
                f"ECHEC : {nb_echeances} echeances alors que la mesure de "
                f"reference en annonce {ECHEANCES_ATTENDUES}."
            )
        print(f"  Verification OK : {ECHEANCES_ATTENDUES:,} echeances attendues et trouvees".replace(",", " "))
    else:
        print("  (echantillon : verification du total ignoree)")

    # Quelques chiffres pour verifier que la consolidation a bien eu l'effet
    # attendu. Sur le jeu complet, le retard moyen des echeances fractionnees
    # doit etre positif.
    #
    # A noter : on obtient +14,05 jours ici, contre +14,00 dans l'analyse
    # exploratoire. L'ecart vient de 24 echeances a montant du nul, que
    # l'analyse excluait par son filtre AMT_INSTALMENT > 0 et que ce job garde.
    # Les deux chiffres sont justes, ils portent sur des populations un peu
    # differentes. Verifie le 28/08/2026.
    fractionnees = echeances.filter(F.col("NB_VERSEMENTS") > 1)
    resume = fractionnees.select(
        F.count("*").alias("nb"),
        F.avg("RETARD_JOURS").alias("retard_moyen"),
        F.avg("TAUX_PAIEMENT").alias("taux_moyen"),
    ).collect()[0]

    print()
    print("Echeances reglees en plusieurs versements :")
    print(f"  nombre        : {resume['nb']:,}".replace(",", " "))
    if resume["retard_moyen"] is not None:
        print(f"  retard moyen  : {resume['retard_moyen']:+.2f} jours")
        print(f"  taux de paiement moyen : {resume['taux_moyen']:.4f}")

    destination = commun.ecrire(echeances, "clean", "installments_consolide")
    print()
    print(f"Ecrit dans {destination}")

    spark.stop()


if __name__ == "__main__":
    main()
