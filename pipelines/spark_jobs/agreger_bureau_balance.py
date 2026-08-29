"""Agrege l'historique mensuel des credits declares au bureau externe.

Source a priorite basse assumee, pour deux raisons mesurees : 70 % des dossiers
n'y ont aucune ligne, et l'information de retard y est rare — 1,26 % seulement
des mois portent un statut de retard.

PARTICULARITE : c'est la seule table du projet qui ne porte PAS l'identifiant du
dossier. Elle est rattachee a un credit externe (SK_ID_BUREAU), lui-meme
rattache a un dossier par la table bureau. Il faut donc une jointure
intermediaire.

    bureau_balance  --SK_ID_BUREAU-->  bureau  --SK_ID_CURR-->  dossier

Cette jointure est aussi la raison pour laquelle 43 041 credits de cette table
sont orphelins : ils referencent un SK_ID_BUREAU absent de bureau.csv. Ils sont
ecartes par la jointure interne, et le job en journalise le volume.

Le statut mensuel se lit ainsi :

    C  credit clos          47,0 %
    0  aucun retard         28,7 %
    X  statut inconnu       22,9 %
    1 a 5  retard croissant  1,3 %  (5 = le plus grave)

Entree : raw/bureau_balance.csv       -> 27 299 925 lignes mensuelles
         raw/bureau.csv               -> pour retrouver le dossier
Sortie : curated/bureau_balance_agrege -> une ligne par dossier
"""

import os
import sys

from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commun

# Les statuts de retard, du plus leger au plus grave. Les autres valeurs — C,
# 0, X — ne sont pas des retards.
STATUTS_RETARD = ["1", "2", "3", "4", "5"]

# Mesure du premier passage complet, le 28/08/2026.
DOSSIERS_ATTENDUS = 134_542


def agreger_par_credit(mois):
    """Premier temps : une ligne par credit externe."""

    # Gravite du retard, sous forme de nombre. Un statut "3" pese plus qu'un
    # statut "1" ; les statuts C, 0 et X valent zero, faute de retard.
    #
    # On construit la conversion explicitement plutot que de convertir le texte
    # en nombre : "X" et "C" produiraient des NULL silencieux, qu'on
    # confondrait ensuite avec une absence de donnee.
    gravite = F.lit(0)
    for niveau in STATUTS_RETARD:
        gravite = F.when(F.col("STATUS") == niveau, int(niveau)).otherwise(gravite)
    mois = mois.withColumn("gravite", gravite)

    return mois.groupBy("SK_ID_BUREAU").agg(
        F.count("*").alias("nb_mois"),
        F.max("gravite").alias("gravite_max"),
        F.avg("gravite").alias("gravite_moyenne"),
        F.sum(F.when(F.col("gravite") > 0, 1).otherwise(0)).alias("nb_mois_retard"),
        # Part de mois ou le credit etait deja clos : un historique surtout
        # compose de credits soldes est rassurant.
        F.avg(F.when(F.col("STATUS") == "C", 1.0).otherwise(0.0)).alias("part_clos"),
        F.avg(F.when(F.col("STATUS") == "X", 1.0).otherwise(0.0)).alias("part_inconnu"),
        F.max("MONTHS_BALANCE").alias("dernier_mois"),
    )


def agreger_par_dossier(credits):
    """Second temps : une ligne par dossier."""

    dossiers = credits.groupBy("SK_ID_CURR").agg(
        F.count("*").alias("BB_NB_CREDITS"),
        F.sum("nb_mois").alias("BB_NB_MOIS"),
        # Le pire retard jamais atteint, tous credits confondus. C'est la
        # variable la plus parlante de cette source.
        F.max("gravite_max").alias("BB_GRAVITE_MAX"),
        F.avg("gravite_moyenne").alias("BB_GRAVITE_MEAN"),
        F.sum("nb_mois_retard").alias("BB_NB_MOIS_RETARD"),
        F.avg("part_clos").alias("BB_PART_CLOS"),
        F.avg("part_inconnu").alias("BB_PART_INCONNU"),
        F.max("dernier_mois").alias("BB_DERNIER_MOIS"),
    )

    dossiers = dossiers.withColumn(
        "BB_PART_MOIS_RETARD",
        F.when(
            F.col("BB_NB_MOIS") > 0,
            F.col("BB_NB_MOIS_RETARD") / F.col("BB_NB_MOIS"),
        ),
    )

    dossiers = dossiers.withColumn("BB_PRESENT", F.lit(1))
    return dossiers


def main():
    echantillon = int(os.environ.get("CREDISCORE_ECHANTILLON", "0"))
    memoire = int(os.environ.get("CREDISCORE_MEMOIRE_GO", "4"))

    spark = commun.creer_session("agreger-bureau-balance", memoire_go=memoire)

    print("Lecture de bureau_balance.csv")
    mois = commun.lire_source(spark, "bureau_balance.csv", echantillon=echantillon)
    print(f"  {mois.count():,} lignes mensuelles".replace(",", " "))

    credits = agreger_par_credit(mois)
    nb_credits = credits.count()
    print(f"  {nb_credits:,} credits externes suivis".replace(",", " "))

    # Jointure intermediaire pour retrouver le dossier. On ne lit que les deux
    # colonnes utiles de bureau.csv : inutile de charger les quinze autres.
    print("Jointure avec bureau.csv pour retrouver les dossiers")
    lien = commun.lire_source(spark, "bureau.csv").select("SK_ID_BUREAU", "SK_ID_CURR")
    credits = credits.join(lien, on="SK_ID_BUREAU", how="inner")

    nb_rattaches = credits.count()
    orphelins = nb_credits - nb_rattaches
    print(f"  {nb_rattaches:,} credits rattaches a un dossier".replace(",", " "))
    print(
        f"  {orphelins:,} credits orphelins ecartes "
        f"({orphelins / nb_credits * 100:.1f} %)".replace(",", " ")
    )

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

    variables = [c for c in dossiers.columns if c.startswith("BB_")]
    print(f"  {len(variables)} variables produites")

    print()
    print("Ce que dit l'historique du bureau :")
    resume = dossiers.select(
        F.round(F.avg("BB_NB_MOIS"), 1).alias("mois_moyen"),
        F.round(F.avg("BB_PART_MOIS_RETARD") * 100, 2).alias("part_retard"),
        F.round(F.avg("BB_PART_CLOS") * 100, 1).alias("part_clos"),
        F.sum(F.when(F.col("BB_GRAVITE_MAX") >= 3, 1).otherwise(0)).alias("graves"),
    ).collect()[0]
    print(f"  mois d'historique par dossier      : {resume['mois_moyen']}")
    print(f"  part de mois en retard             : {resume['part_retard']} %")
    print(f"  part de mois en credit clos        : {resume['part_clos']} %")
    print(
        f"  dossiers avec un retard grave (>=3): "
        f"{resume['graves']:,}".replace(",", " ")
    )

    destination = commun.ecrire(dossiers, "curated", "bureau_balance_agrege")
    print()
    print(f"Ecrit dans {destination}")

    spark.stop()


if __name__ == "__main__":
    main()
