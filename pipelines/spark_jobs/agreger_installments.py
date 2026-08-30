"""Transforme les echeances consolidees en variables par dossier.

L'agregation se fait en DEUX TEMPS, et ce n'est pas un detail :

  echeances  ->  par credit (SK_ID_PREV)  ->  par dossier (SK_ID_CURR)

Agreger directement au dossier donnerait plus de poids aux credits comptant
beaucoup d'echeances. Un client avec un credit de 60 mensualites et un autre de
6 verrait le premier peser dix fois plus dans sa moyenne. Le passage par le
credit remet les deux sur un pied d'egalite.

Entree : clean/installments_consolide   -> 12 951 918 echeances
Sortie : curated/installments_agrege    -> 339 587 dossiers
"""

import os
import sys

from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commun
import pseudonyme

# Un an d'historique. Les jours sont negatifs et comptes depuis la demande,
# donc les 12 derniers mois sont les echeances dont le jour est superieur
# a -365.
UN_AN_EN_JOURS = -365

# Nombre de dossiers ayant au moins une echeance, mesure le 28/08/2026 :
#
#   291 643 dans application_train  (94,8 % des 307 511)
#  + 47 944 dans application_test   (98,4 % des 48 744)
#  = 339 587, et aucun dossier hors de ces deux fichiers.
#
# Attention au piege : la couverture de 94,1 % citee dans schema_jointures.md
# porte sur application_train SEUL. Or ce fichier contient aussi les dossiers
# a scorer. Un premier seuil pose a 285 000-295 000 faisait echouer le job a
# tort — c'etait le seuil qui etait faux, pas l'agregation.
DOSSIERS_ATTENDUS = 339_587


def agreger_par_credit(echeances):
    """Premier temps : une ligne par credit anterieur."""

    # On resume le comportement de paiement sur chaque credit. Ces colonnes ne
    # sortiront pas du job : elles servent de marche intermediaire.
    return echeances.groupBy("SK_ID_PREV").agg(
        F.first("SK_ID_CURR").alias("SK_ID_CURR"),
        F.count("*").alias("nb_echeances"),
        F.avg("RETARD_JOURS").alias("retard_moyen"),
        F.max("RETARD_JOURS").alias("retard_max"),
        F.sum("RETARD_JOURS").alias("retard_total"),
        # Nombre d'echeances payees en retard. La comparaison rend un booleen,
        # que l'on transforme en 1 ou 0 pour pouvoir le sommer.
        F.sum(F.when(F.col("RETARD_JOURS") > 0, 1).otherwise(0)).alias("nb_retards"),
        F.avg("TAUX_PAIEMENT").alias("taux_moyen"),
        F.min("TAUX_PAIEMENT").alias("taux_min"),
        F.sum("MONTANT_DU").alias("montant_du"),
        F.sum("MONTANT_PAYE").alias("montant_paye"),
        F.sum(F.when(F.col("JAMAIS_PAYEE"), 1).otherwise(0)).alias("nb_jamais_payees"),
    )


def retard_recent(echeances):
    """Retard maximal sur les douze derniers mois, par dossier.

    Le comportement recent predit mieux que l'ancien : un client qui payait mal
    il y a quatre ans mais bien depuis un an n'est pas le meme risque que
    l'inverse. On calcule donc cette variable a part, directement sur les
    echeances, en filtrant sur la fenetre de temps.
    """
    recentes = echeances.filter(F.col("JOUR_ECHEANCE") >= UN_AN_EN_JOURS)
    return recentes.groupBy("SK_ID_CURR").agg(
        F.max("RETARD_JOURS").alias("INSTAL_RETARD_MAX_12M"),
        F.count("*").alias("INSTAL_NB_ECHEANCES_12M"),
    )


def agreger_par_dossier(credits):
    """Second temps : une ligne par dossier, avec les variables finales."""

    dossiers = credits.groupBy("SK_ID_CURR").agg(
        # Combien de credits anterieurs, et combien d'echeances au total.
        F.count("*").alias("INSTAL_NB_CREDITS"),
        F.sum("nb_echeances").alias("INSTAL_NB_ECHEANCES"),
        # Le retard, vu sous quatre angles. La moyenne dit l'habitude, le
        # maximum dit le pire incident, l'ecart-type dit la regularite, et la
        # somme dit le cumul.
        F.avg("retard_moyen").alias("INSTAL_RETARD_JOURS_MEAN"),
        F.max("retard_max").alias("INSTAL_RETARD_JOURS_MAX"),
        F.stddev("retard_moyen").alias("INSTAL_RETARD_JOURS_STD"),
        F.sum("retard_total").alias("INSTAL_RETARD_JOURS_SUM"),
        # Combien de fois en retard.
        F.sum("nb_retards").alias("INSTAL_NB_RETARDS"),
        # Le taux de paiement : en moyenne, et dans le pire des cas.
        F.avg("taux_moyen").alias("INSTAL_TAUX_PAIEMENT_MEAN"),
        F.min("taux_min").alias("INSTAL_TAUX_PAIEMENT_MIN"),
        # Les montants, pour mesurer l'ecart entre du et paye.
        F.sum("montant_du").alias("INSTAL_MONTANT_DU_SUM"),
        F.sum("montant_paye").alias("INSTAL_MONTANT_PAYE_SUM"),
        F.sum("nb_jamais_payees").alias("INSTAL_NB_JAMAIS_PAYEES"),
    )

    # Part des echeances payees en retard. Plus parlante que le nombre brut :
    # trois retards sur cinq echeances n'est pas trois retards sur cent.
    dossiers = dossiers.withColumn(
        "INSTAL_PART_RETARDS",
        F.when(
            F.col("INSTAL_NB_ECHEANCES") > 0,
            F.col("INSTAL_NB_RETARDS") / F.col("INSTAL_NB_ECHEANCES"),
        ),
    )

    # Ce qui reste du apres tous les paiements. Positif = le client doit encore.
    dossiers = dossiers.withColumn(
        "INSTAL_RESTE_DU",
        F.col("INSTAL_MONTANT_DU_SUM") - F.col("INSTAL_MONTANT_PAYE_SUM"),
    )

    # Indicateur de presence. Apres la jointure avec le socle, les dossiers
    # sans historique auront NULL partout ; cette colonne permet de les
    # reconnaitre sans confondre "pas d'historique" et "zero retard".
    dossiers = dossiers.withColumn("INSTAL_PRESENT", F.lit(1))

    return dossiers


def main():
    echantillon = int(os.environ.get("CREDISCORE_ECHANTILLON", "0"))
    memoire = int(os.environ.get("CREDISCORE_MEMOIRE_GO", "4"))

    spark = commun.creer_session("agreger-installments", memoire_go=memoire)

    print("Lecture de clean/installments_consolide")
    echeances = commun.lire(spark, "clean", "installments_consolide")
    if echantillon:
        echeances = echeances.limit(echantillon)
    print(f"  {echeances.count():,} echeances".replace(",", " "))

    credits = agreger_par_credit(echeances)
    print(f"  {credits.count():,} credits anterieurs".replace(",", " "))

    dossiers = agreger_par_dossier(credits)

    # On rattache le comportement recent, calcule a part.
    recent = retard_recent(echeances)
    dossiers = dossiers.join(recent, on="SK_ID_CURR", how="left")
    dossiers.cache()

    nb_dossiers = dossiers.count()
    print(f"  {nb_dossiers:,} dossiers".replace(",", " "))

    # Deux verifications simples mais qui attrapent les erreurs les plus
    # courantes : une jointure qui duplique, et une agregation qui perd des
    # dossiers en route.
    nb_distincts = dossiers.select("SK_ID_CURR").distinct().count()
    if nb_distincts != nb_dossiers:
        raise SystemExit(
            f"ECHEC : {nb_dossiers} lignes pour {nb_distincts} dossiers "
            f"distincts. La sortie doit avoir une seule ligne par dossier."
        )
    print("  Verification OK : une seule ligne par dossier")

    if echantillon == 0:
        if nb_dossiers != DOSSIERS_ATTENDUS:
            raise SystemExit(
                f"ECHEC : {nb_dossiers} dossiers alors que la mesure de "
                f"reference en annonce {DOSSIERS_ATTENDUS}."
            )
        couverture = nb_dossiers / 356_255 * 100
        print(
            f"  Verification OK : {nb_dossiers:,} dossiers, "
            f"soit {couverture:.1f} % des demandes".replace(",", " ")
        )

    variables = [c for c in dossiers.columns if c.startswith("INSTAL_")]
    print(f"  {len(variables)} variables produites")

    print()
    print("Apercu sur trois dossiers :")
    # On affiche le pseudonyme et non l'identifiant reel : ce texte finit dans
    # les journaux Airflow, qui sont moins proteges que les donnees (C-7).
    pseudonyme.ajouter_pseudonyme(dossiers).select(
        "PSEUDO",
        "INSTAL_NB_CREDITS",
        "INSTAL_NB_ECHEANCES",
        F.round("INSTAL_RETARD_JOURS_MEAN", 2).alias("RETARD_MOYEN"),
        "INSTAL_NB_RETARDS",
        F.round("INSTAL_PART_RETARDS", 3).alias("PART_RETARDS"),
    ).show(3, truncate=False)

    destination = commun.ecrire(dossiers, "curated", "installments_agrege")
    print(f"Ecrit dans {destination}")

    spark.stop()


if __name__ == "__main__":
    main()
