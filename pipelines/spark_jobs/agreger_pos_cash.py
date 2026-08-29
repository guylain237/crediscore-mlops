"""Agrege l'etat mensuel des credits POS et tresorerie en variables par dossier.

Cette table suit, mois par mois, la tenue de chaque credit anterieur : combien
de mensualites restent, et surtout combien de jours de retard le client accuse.

Deux colonnes de retard cohabitent, et la nuance compte :

  SK_DPD      tout retard, meme d'un euro et d'un jour
  SK_DPD_DEF  seulement les retards depassant un seuil de tolerance

La premiere capte les negligences, la seconde les vrais incidents. On garde
les deux : un client souvent en retard de trois jours n'a pas le meme profil
qu'un client une fois en retard de trois mois.

Comme pour installments, l'agregation se fait en DEUX TEMPS — par credit puis
par dossier — pour qu'un credit suivi sur 60 mois ne pese pas dix fois plus
qu'un credit suivi sur 6.

Entree : raw/POS_CASH_balance.csv   -> 10 001 358 lignes mensuelles
Sortie : curated/pos_cash_agrege    -> une ligne par dossier
"""

import os
import sys

from pyspark.sql import Window
from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commun

# Mesure du premier passage complet, le 28/08/2026. La valeur reste a None
# tant qu'elle n'est pas mesuree : un seuil invente ferait echouer le job a
# tort, comme cela s'est produit avec agreger_installments.
DOSSIERS_ATTENDUS = 337_252


def agreger_par_credit(mois):
    """Premier temps : une ligne par credit anterieur."""

    # Le statut du DERNIER mois observe. Les mois sont numerotes en negatif
    # depuis la demande, donc le mois le plus recent est celui dont
    # MONTHS_BALANCE est le plus grand.
    #
    # On utilise une fenetre plutot qu'une jointure : Spark classe les lignes
    # de chaque credit et on ne garde que la premiere.
    fenetre = Window.partitionBy("SK_ID_PREV").orderBy(F.col("MONTHS_BALANCE").desc())
    mois = mois.withColumn("rang", F.row_number().over(fenetre))
    mois = mois.withColumn(
        "statut_final", F.when(F.col("rang") == 1, F.col("NAME_CONTRACT_STATUS"))
    )

    return mois.groupBy("SK_ID_PREV").agg(
        F.first("SK_ID_CURR").alias("SK_ID_CURR"),
        F.count("*").alias("nb_mois"),
        # Retard tolerant.
        F.avg("SK_DPD").alias("dpd_moyen"),
        F.max("SK_DPD").alias("dpd_max"),
        # Retard strict : les incidents qui comptent vraiment.
        F.avg("SK_DPD_DEF").alias("dpd_def_moyen"),
        F.max("SK_DPD_DEF").alias("dpd_def_max"),
        # Nombre de mois ou le client etait en retard.
        F.sum(F.when(F.col("SK_DPD") > 0, 1).otherwise(0)).alias("nb_mois_retard"),
        F.sum(F.when(F.col("SK_DPD_DEF") > 0, 1).otherwise(0)).alias(
            "nb_mois_retard_grave"
        ),
        # Mensualites restant a payer au dernier mois observe.
        F.min("CNT_INSTALMENT_FUTURE").alias("mensualites_restantes"),
        # Mois le plus recent, et statut a cette date.
        F.max("MONTHS_BALANCE").alias("dernier_mois"),
        F.max("statut_final").alias("statut_final"),
    )


def agreger_par_dossier(credits):
    """Second temps : une ligne par dossier."""

    dossiers = credits.groupBy("SK_ID_CURR").agg(
        F.count("*").alias("POS_NB_CREDITS"),
        F.sum("nb_mois").alias("POS_NB_MOIS"),
        # Retard tolerant, vu en moyenne et au pire.
        F.avg("dpd_moyen").alias("POS_DPD_MEAN"),
        F.max("dpd_max").alias("POS_DPD_MAX"),
        # Retard strict.
        F.avg("dpd_def_moyen").alias("POS_DPD_DEF_MEAN"),
        F.max("dpd_def_max").alias("POS_DPD_DEF_MAX"),
        F.sum("nb_mois_retard").alias("POS_NB_MOIS_RETARD"),
        F.sum("nb_mois_retard_grave").alias("POS_NB_MOIS_RETARD_GRAVE"),
        # Combien de credits sont encore en cours, combien sont soldes.
        F.sum(F.when(F.col("statut_final") == "Active", 1).otherwise(0)).alias(
            "POS_NB_ACTIFS"
        ),
        F.sum(F.when(F.col("statut_final") == "Completed", 1).otherwise(0)).alias(
            "POS_NB_TERMINES"
        ),
        # Mensualites encore dues, tous credits confondus.
        F.sum("mensualites_restantes").alias("POS_MENSUALITES_RESTANTES"),
        # Anciennete du suivi le plus recent.
        F.max("dernier_mois").alias("POS_DERNIER_MOIS"),
    )

    # Part des mois passes en retard. Plus parlante que le compte : dix mois de
    # retard sur douze n'est pas dix mois sur deux cents.
    dossiers = dossiers.withColumn(
        "POS_PART_MOIS_RETARD",
        F.when(
            F.col("POS_NB_MOIS") > 0,
            F.col("POS_NB_MOIS_RETARD") / F.col("POS_NB_MOIS"),
        ),
    )
    dossiers = dossiers.withColumn(
        "POS_PART_MOIS_RETARD_GRAVE",
        F.when(
            F.col("POS_NB_MOIS") > 0,
            F.col("POS_NB_MOIS_RETARD_GRAVE") / F.col("POS_NB_MOIS"),
        ),
    )

    dossiers = dossiers.withColumn("POS_PRESENT", F.lit(1))
    return dossiers


def main():
    echantillon = int(os.environ.get("CREDISCORE_ECHANTILLON", "0"))
    memoire = int(os.environ.get("CREDISCORE_MEMOIRE_GO", "4"))

    spark = commun.creer_session("agreger-pos-cash", memoire_go=memoire)

    print("Lecture de POS_CASH_balance.csv")
    mois = commun.lire_source(spark, "POS_CASH_balance.csv", echantillon=echantillon)
    print(f"  {mois.count():,} lignes mensuelles".replace(",", " "))

    credits = agreger_par_credit(mois)
    print(f"  {credits.count():,} credits suivis".replace(",", " "))

    dossiers = agreger_par_dossier(credits)
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

    variables = [c for c in dossiers.columns if c.startswith("POS_")]
    print(f"  {len(variables)} variables produites")

    print()
    print("Ce que disent les retards :")
    resume = dossiers.select(
        F.round(F.avg("POS_PART_MOIS_RETARD") * 100, 2).alias("part_retard"),
        F.round(F.avg("POS_PART_MOIS_RETARD_GRAVE") * 100, 2).alias("part_grave"),
        F.round(F.avg("POS_DPD_MAX"), 1).alias("dpd_max_moyen"),
        F.sum(F.when(F.col("POS_DPD_DEF_MAX") > 0, 1).otherwise(0)).alias("avec_incident"),
    ).collect()[0]
    print(f"  part de mois en retard (tolerant) : {resume['part_retard']} %")
    print(f"  part de mois en retard (strict)   : {resume['part_grave']} %")
    print(f"  retard maximal moyen              : {resume['dpd_max_moyen']} jours")
    print(
        f"  dossiers avec au moins un incident grave : "
        f"{resume['avec_incident']:,}".replace(",", " ")
    )

    destination = commun.ecrire(dossiers, "curated", "pos_cash_agrege")
    print()
    print(f"Ecrit dans {destination}")

    spark.stop()


if __name__ == "__main__":
    main()
