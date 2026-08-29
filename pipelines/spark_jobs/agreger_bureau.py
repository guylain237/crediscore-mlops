"""Agrege les credits detenus chez d'autres etablissements.

C'est la seule source EXTERNE du projet. Elle apporte ce que CrediScore ne peut
pas savoir seul : combien ce demandeur doit ailleurs, et comment il s'y tient.
Un client irreprochable chez nous peut etre surendette chez trois concurrents.

La table est au grain du credit externe (SK_ID_BUREAU), avec l'identifiant du
dossier. Une seule marche d'agregation suffit donc.

Deux anomalies de donnees sont traitees ici, toutes deux mesurees le
28/08/2026 :

  1. DAYS_CREDIT_ENDDATE porte une sentinelle (voir plus bas).
  2. AMT_CREDIT_SUM_DEBT compte 8 418 valeurs negatives, conservees telles
     quelles : une dette negative signifie un trop-percu en faveur du client.
     C'est une information, pas une erreur.

Entree : raw/bureau.csv          -> 1 716 428 credits externes
Sortie : curated/bureau_agrege   -> une ligne par dossier
"""

import os
import sys

from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commun

# SENTINELLE DE DATE DE FIN.
#
# Le plan de features prevoyait un ecretage au 99e centile. La mesure montre
# que ce remede ne fonctionne pas : le 99e centile vaut deja 31 029 jours, soit
# 85 ans. Ecreter la ne retirerait rien.
#
# Ce que la mesure revele en revanche :
#
#   38 454 lignes au-dela de 55 ans dans le futur (2,39 %)
#   groupees entre 27 214 et 31 199 jours
#   dont 99,5 % sont des CARTES DE CREDIT
#
# Un credit renouvelable n'a pas de date de fin. Le bureau code donc cette
# absence par une date tres lointaine. C'est une sentinelle, exactement comme
# DAYS_EMPLOYED = 365243 pour les retraites.
#
# On la neutralise plutot que de l'ecreter : ecreter inventerait une date de
# fin a 30 ans pour un credit qui n'en a pas. Le NULL dit la verite — on ne
# sait pas, parce qu'il n'y en a pas.
#
# Seuil retenu : 30 ans. Aucun credit a la consommation ni aucune carte ne
# court au-dela ; les 31 296 lignes de la tranche 10-30 ans, qui contiennent
# les prets immobiliers legitimes, sont conservees.
JOURS_FIN_PLAUSIBLE_MAX = 10_950

# Les quatre etats possibles d'un credit externe. Repartition mesuree :
# 62,6 % clos, 37,0 % actifs, 0,4 % vendus, et une poignee d'impayes.
ETATS = {
    "Active": "ACTIFS",
    "Closed": "CLOS",
    "Sold": "VENDUS",
    "Bad debt": "IMPAYES",
}

# Les deux types dominants : 72,7 % de credit a la consommation, 23,6 % de
# cartes. Les autres types pesent moins de 2 % chacun et sont laisses de cote.
TYPES = {
    "Consumer credit": "CONSO",
    "Credit card": "CARTE",
}

# Mesure du premier passage complet, le 28/08/2026.
DOSSIERS_ATTENDUS = 305_811


def part_par_categorie(colonne, correspondances, prefixe):
    """Une part par modalite, plutot qu'un encodage brut de la categorie."""
    return [
        F.avg(F.when(F.col(colonne) == valeur, 1.0).otherwise(0.0)).alias(
            f"{prefixe}_PART_{nom}"
        )
        for valeur, nom in correspondances.items()
    ]


def neutraliser_sentinelle(credits):
    """Remplace les dates de fin implausibles par NULL, et garde la trace."""

    credits = credits.withColumn(
        "fin_sans_echeance",
        F.when(F.col("DAYS_CREDIT_ENDDATE") > JOURS_FIN_PLAUSIBLE_MAX, 1).otherwise(0),
    )
    return credits.withColumn(
        "DAYS_CREDIT_ENDDATE",
        F.when(
            F.col("DAYS_CREDIT_ENDDATE") <= JOURS_FIN_PLAUSIBLE_MAX,
            F.col("DAYS_CREDIT_ENDDATE"),
        ),
    )


def agreger(credits):
    """Une ligne par dossier, a partir des credits externes."""

    agregations = [
        # Combien de credits ailleurs, et de combien de natures differentes.
        F.count("*").alias("BUREAU_NB_CREDITS"),
        F.countDistinct("CREDIT_TYPE").alias("BUREAU_NB_TYPES"),
        F.sum(F.when(F.col("CREDIT_ACTIVE") == "Active", 1).otherwise(0)).alias(
            "BUREAU_NB_ACTIFS"
        ),
        # L'exposition : ce que le client doit encore ailleurs.
        F.sum("AMT_CREDIT_SUM_DEBT").alias("BUREAU_DETTE_SUM"),
        F.max("AMT_CREDIT_SUM_DEBT").alias("BUREAU_DETTE_MAX"),
        F.avg("AMT_CREDIT_SUM_DEBT").alias("BUREAU_DETTE_MEAN"),
        # Le plafond accorde par les autres etablissements.
        F.sum("AMT_CREDIT_SUM").alias("BUREAU_PLAFOND_SUM"),
        F.max("AMT_CREDIT_SUM").alias("BUREAU_PLAFOND_MAX"),
        # Les impayes en cours. Une seule ligne suffit a changer un profil.
        F.sum("AMT_CREDIT_SUM_OVERDUE").alias("BUREAU_IMPAYE_SUM"),
        F.max("AMT_CREDIT_SUM_OVERDUE").alias("BUREAU_IMPAYE_MAX"),
        F.max("CREDIT_DAY_OVERDUE").alias("BUREAU_JOURS_IMPAYE_MAX"),
        F.sum(F.when(F.col("AMT_CREDIT_SUM_OVERDUE") > 0, 1).otherwise(0)).alias(
            "BUREAU_NB_AVEC_IMPAYE"
        ),
        # Le pire impaye jamais constate, meme s'il est regularise depuis.
        # Absent dans 66 % des cas : le NULL est conserve.
        F.max("AMT_CREDIT_MAX_OVERDUE").alias("BUREAU_IMPAYE_HISTORIQUE_MAX"),
        # Une prolongation est une renegociation : le client n'arrivait pas a
        # tenir l'echeancier initial.
        F.sum("CNT_CREDIT_PROLONG").alias("BUREAU_NB_PROLONGATIONS"),
        # Charge mensuelle chez les autres. Absente dans 69 % des cas.
        F.sum("AMT_ANNUITY").alias("BUREAU_ANNUITE_SUM"),
        F.avg("AMT_ANNUITY").alias("BUREAU_ANNUITE_MEAN"),
        # Anciennete. Les jours sont negatifs : le MAXIMUM est le credit le
        # plus recent, le MINIMUM le plus ancien.
        F.max("DAYS_CREDIT").alias("BUREAU_JOURS_CREDIT_RECENT"),
        F.min("DAYS_CREDIT").alias("BUREAU_JOURS_CREDIT_ANCIEN"),
        F.max("DAYS_CREDIT_UPDATE").alias("BUREAU_JOURS_MAJ_RECENTE"),
        # Date de fin la plus lointaine, une fois la sentinelle neutralisee.
        F.max("DAYS_CREDIT_ENDDATE").alias("BUREAU_JOURS_FIN_MAX"),
        # Combien de credits sans echeance, c'est-a-dire renouvelables.
        F.sum("fin_sans_echeance").alias("BUREAU_NB_SANS_ECHEANCE"),
    ]
    agregations += part_par_categorie("CREDIT_ACTIVE", ETATS, "BUREAU")
    agregations += part_par_categorie("CREDIT_TYPE", TYPES, "BUREAU")

    dossiers = credits.groupBy("SK_ID_CURR").agg(*agregations)

    # Taux d'utilisation : quelle part du plafond accorde est reellement
    # consommee. Un client qui utilise 95 % de ce qu'on lui a accorde n'a plus
    # de marge de manoeuvre.
    dossiers = dossiers.withColumn(
        "BUREAU_TAUX_UTILISATION",
        F.when(
            F.col("BUREAU_PLAFOND_SUM") > 0,
            F.col("BUREAU_DETTE_SUM") / F.col("BUREAU_PLAFOND_SUM"),
        ),
    )

    # Duree de l'historique connu chez les autres etablissements.
    dossiers = dossiers.withColumn(
        "BUREAU_DUREE_HISTORIQUE_JOURS",
        F.col("BUREAU_JOURS_CREDIT_RECENT") - F.col("BUREAU_JOURS_CREDIT_ANCIEN"),
    )

    dossiers = dossiers.withColumn("BUREAU_PRESENT", F.lit(1))
    return dossiers


def main():
    echantillon = int(os.environ.get("CREDISCORE_ECHANTILLON", "0"))
    memoire = int(os.environ.get("CREDISCORE_MEMOIRE_GO", "4"))

    spark = commun.creer_session("agreger-bureau", memoire_go=memoire)

    print("Lecture de bureau.csv")
    credits = commun.lire_source(spark, "bureau.csv", echantillon=echantillon)
    nb_credits = credits.count()
    print(f"  {nb_credits:,} credits externes".replace(",", " "))

    credits = neutraliser_sentinelle(credits)
    nb_sentinelles = credits.filter(F.col("fin_sans_echeance") == 1).count()
    print(
        f"  {nb_sentinelles:,} dates de fin neutralisees "
        f"({nb_sentinelles / nb_credits * 100:.2f} %)".replace(",", " ")
    )

    dossiers = agreger(credits)
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

    variables = [c for c in dossiers.columns if c.startswith("BUREAU_")]
    print(f"  {len(variables)} variables produites")

    print()
    print("L'exposition chez les autres etablissements :")
    resume = dossiers.select(
        F.round(F.avg("BUREAU_NB_CREDITS"), 1).alias("nb_moyen"),
        F.round(F.avg("BUREAU_NB_ACTIFS"), 1).alias("actifs_moyen"),
        F.round(F.avg("BUREAU_TAUX_UTILISATION") * 100, 1).alias("utilisation"),
        F.sum(F.when(F.col("BUREAU_NB_AVEC_IMPAYE") > 0, 1).otherwise(0)).alias(
            "avec_impaye"
        ),
        F.sum(F.when(F.col("BUREAU_NB_PROLONGATIONS") > 0, 1).otherwise(0)).alias(
            "avec_prolongation"
        ),
    ).collect()[0]
    print(f"  credits par dossier          : {resume['nb_moyen']}")
    print(f"  dont encore actifs           : {resume['actifs_moyen']}")
    print(f"  taux d'utilisation moyen     : {resume['utilisation']} %")
    print(
        f"  dossiers avec un impaye      : {resume['avec_impaye']:,}".replace(",", " ")
    )
    print(
        f"  dossiers avec prolongation   : "
        f"{resume['avec_prolongation']:,}".replace(",", " ")
    )

    destination = commun.ecrire(dossiers, "curated", "bureau_agrege")
    print()
    print(f"Ecrit dans {destination}")

    spark.stop()


if __name__ == "__main__":
    main()
