"""Agrege l'etat mensuel des cartes de credit anterieures.

Source a priorite basse assumee : 74,7 % des dossiers n'ont aucune carte. Pour
le quart restant, elle porte un signal reconnu du secteur — le TAUX
D'UTILISATION. Un client qui frole en permanence son plafond est en tension de
tresorerie, meme s'il paie ses echeances a l'heure.

    TAUX_UTILISATION = AMT_BALANCE / AMT_CREDIT_LIMIT_ACTUAL

Les retraits d'especes sont le second signal : payer ses courses par carte est
ordinaire, retirer du liquide au distributeur avec une carte de credit l'est
beaucoup moins. C'est souvent le signe qu'on emprunte pour boucler le mois.

Agregation en deux temps, comme pour POS_CASH : par carte puis par dossier.

Entree : raw/credit_card_balance.csv   -> 3 840 312 lignes mensuelles
Sortie : curated/credit_card_agrege    -> une ligne par dossier
"""

import os
import sys

from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commun

# Douze derniers mois. Les mois sont numerotes en negatif depuis la demande.
UN_AN_EN_MOIS = -12

# Mesure du premier passage complet, le 28/08/2026.
DOSSIERS_ATTENDUS = 103_558


def agreger_par_carte(mois):
    """Premier temps : une ligne par carte."""

    # Taux d'utilisation du plafond. Le denominateur peut valoir zero — une
    # carte au plafond nul existe dans les donnees — d'ou le garde-fou.
    mois = mois.withColumn(
        "taux_utilisation",
        F.when(
            F.col("AMT_CREDIT_LIMIT_ACTUAL") > 0,
            F.col("AMT_BALANCE") / F.col("AMT_CREDIT_LIMIT_ACTUAL"),
        ),
    )

    # Le meme taux, mais sur les douze derniers mois seulement. Comparer les
    # deux revient a mesurer une tendance : si le taux recent depasse le taux
    # historique, la situation se degrade.
    mois = mois.withColumn(
        "taux_recent",
        F.when(F.col("MONTHS_BALANCE") >= UN_AN_EN_MOIS, F.col("taux_utilisation")),
    )

    return mois.groupBy("SK_ID_PREV").agg(
        F.first("SK_ID_CURR").alias("SK_ID_CURR"),
        F.count("*").alias("nb_mois"),
        F.avg("taux_utilisation").alias("taux_moyen"),
        F.max("taux_utilisation").alias("taux_max"),
        F.avg("taux_recent").alias("taux_recent_moyen"),
        F.avg("AMT_BALANCE").alias("solde_moyen"),
        F.max("AMT_CREDIT_LIMIT_ACTUAL").alias("plafond_max"),
        # Retraits d'especes au distributeur.
        F.sum("CNT_DRAWINGS_ATM_CURRENT").alias("nb_retraits_especes"),
        F.sum("AMT_DRAWINGS_ATM_CURRENT").alias("montant_retraits_especes"),
        F.sum("CNT_DRAWINGS_CURRENT").alias("nb_operations"),
        # Paiements effectues.
        F.avg("AMT_PAYMENT_TOTAL_CURRENT").alias("paiement_moyen"),
        # Retards, dans les deux acceptions comme pour POS_CASH.
        F.avg("SK_DPD").alias("dpd_moyen"),
        F.max("SK_DPD").alias("dpd_max"),
        F.max("SK_DPD_DEF").alias("dpd_def_max"),
        F.sum(F.when(F.col("SK_DPD") > 0, 1).otherwise(0)).alias("nb_mois_retard"),
    )


def agreger_par_dossier(cartes):
    """Second temps : une ligne par dossier."""

    dossiers = cartes.groupBy("SK_ID_CURR").agg(
        F.count("*").alias("CC_NB_CARTES"),
        F.sum("nb_mois").alias("CC_NB_MOIS"),
        # Le signal principal : l'utilisation du plafond.
        F.avg("taux_moyen").alias("CC_TAUX_UTILISATION_MEAN"),
        F.max("taux_max").alias("CC_TAUX_UTILISATION_MAX"),
        F.avg("taux_recent_moyen").alias("CC_TAUX_UTILISATION_12M"),
        F.avg("solde_moyen").alias("CC_SOLDE_MEAN"),
        F.sum("plafond_max").alias("CC_PLAFOND_SUM"),
        # Les retraits d'especes.
        F.sum("nb_retraits_especes").alias("CC_NB_RETRAITS_ESPECES"),
        F.sum("montant_retraits_especes").alias("CC_MONTANT_RETRAITS_ESPECES"),
        F.sum("nb_operations").alias("CC_NB_OPERATIONS"),
        # Les paiements et les retards.
        F.avg("paiement_moyen").alias("CC_PAIEMENT_MEAN"),
        F.avg("dpd_moyen").alias("CC_DPD_MEAN"),
        F.max("dpd_max").alias("CC_DPD_MAX"),
        F.max("dpd_def_max").alias("CC_DPD_DEF_MAX"),
        F.sum("nb_mois_retard").alias("CC_NB_MOIS_RETARD"),
    )

    # Tendance : le taux recent compare au taux historique. Positif = la
    # situation se degrade, le client consomme davantage son plafond qu'avant.
    dossiers = dossiers.withColumn(
        "CC_TENDANCE_UTILISATION",
        F.col("CC_TAUX_UTILISATION_12M") - F.col("CC_TAUX_UTILISATION_MEAN"),
    )

    # Part des operations qui sont des retraits d'especes.
    dossiers = dossiers.withColumn(
        "CC_PART_RETRAITS_ESPECES",
        F.when(
            F.col("CC_NB_OPERATIONS") > 0,
            F.col("CC_NB_RETRAITS_ESPECES") / F.col("CC_NB_OPERATIONS"),
        ),
    )

    dossiers = dossiers.withColumn("CC_PRESENT", F.lit(1))
    return dossiers


def main():
    echantillon = int(os.environ.get("CREDISCORE_ECHANTILLON", "0"))
    memoire = int(os.environ.get("CREDISCORE_MEMOIRE_GO", "4"))

    spark = commun.creer_session("agreger-credit-card", memoire_go=memoire)

    print("Lecture de credit_card_balance.csv")
    mois = commun.lire_source(spark, "credit_card_balance.csv", echantillon=echantillon)
    print(f"  {mois.count():,} lignes mensuelles".replace(",", " "))

    cartes = agreger_par_carte(mois)
    print(f"  {cartes.count():,} cartes suivies".replace(",", " "))

    dossiers = agreger_par_dossier(cartes)
    dossiers.cache()
    nb_dossiers = dossiers.count()
    print(f"  {nb_dossiers:,} dossiers".replace(",", " "))

    nb_distincts = dossiers.select("SK_ID_CURR").distinct().count()
    if nb_distincts != nb_dossiers:
        raise SystemExit(
            f"ECHEC : {nb_dossiers} lignes pour {nb_distincts} dossiers "
            f"distincts. La sortie doit avoir une seule ligne par dossier."
        )
    print("  Verification OK : une seule ligne par dossier")

    if echantillon == 0 and DOSSIERS_ATTENDUS is not None:
        if nb_dossiers != DOSSIERS_ATTENDUS:
            raise SystemExit(
                f"ECHEC : {nb_dossiers} dossiers alors que la mesure de "
                f"reference en annonce {DOSSIERS_ATTENDUS}."
            )
        print(f"  Verification OK : {DOSSIERS_ATTENDUS:,} dossiers".replace(",", " "))

    variables = [c for c in dossiers.columns if c.startswith("CC_")]
    print(f"  {len(variables)} variables produites")

    print()
    print("Ce que disent les cartes :")
    resume = dossiers.select(
        F.round(F.avg("CC_TAUX_UTILISATION_MEAN") * 100, 1).alias("taux"),
        F.round(F.avg("CC_TENDANCE_UTILISATION") * 100, 1).alias("tendance"),
        F.round(F.avg("CC_PART_RETRAITS_ESPECES") * 100, 1).alias("part_especes"),
        F.sum(F.when(F.col("CC_TAUX_UTILISATION_MAX") > 0.95, 1).otherwise(0)).alias(
            "au_plafond"
        ),
    ).collect()[0]
    print(f"  taux d'utilisation moyen         : {resume['taux']} %")
    print(f"  tendance sur 12 mois             : {resume['tendance']:+} point(s)")
    print(f"  part de retraits d'especes       : {resume['part_especes']} %")
    print(
        f"  dossiers ayant frole le plafond  : "
        f"{resume['au_plafond']:,}".replace(",", " ")
    )

    destination = commun.ecrire(dossiers, "curated", "credit_card_agrege")
    print()
    print(f"Ecrit dans {destination}")

    spark.stop()


if __name__ == "__main__":
    main()
