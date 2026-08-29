"""Agrege les demandes anterieures chez CrediScore en variables par dossier.

Cette table decrit l'historique de la relation avec l'etablissement. Le signal
le plus fort qu'elle porte est le taux de REFUS anterieur : si CrediScore avait
deja juge ce demandeur trop risque par le passe, c'est une information de
premier ordre.

Contrairement a installments_payments, aucune consolidation n'est necessaire :
la table est deja au bon grain, une ligne par demande anterieure, et l'analyse
n'y a trouve aucun doublon.

Entree : raw/previous_application.csv  -> 1 670 214 demandes anterieures
Sortie : curated/previous_agrege       -> une ligne par dossier
"""

import os
import sys

from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commun

# Les quatre issues possibles d'une demande, et le nom court qu'on leur donne
# dans les variables produites. Repartition mesuree : 63,5 % acceptees,
# 17,7 % annulees, 17,1 % refusees, 1,6 % offres non utilisees.
ISSUES = {
    "Approved": "ACCEPTEES",
    "Refused": "REFUSEES",
    "Canceled": "ANNULEES",
    "Unused offer": "NON_UTILISEES",
}

# Les trois types de credit proposes par l'etablissement.
TYPES_CONTRAT = {
    "Consumer loans": "CONSO",
    "Cash loans": "TRESORERIE",
    "Revolving loans": "RENOUVELABLE",
}

# Mesure du premier passage complet, le 28/08/2026.
DOSSIERS_ATTENDUS = 338_857


def part_par_categorie(colonne, correspondances, prefixe):
    """Construit une part pour chaque valeur d'une colonne categorielle.

    Plutot que d'encoder la categorie telle quelle, on calcule la PART de
    chaque issue dans l'historique du client. Un client ayant eu deux refus sur
    trois demandes n'est pas dans la meme situation qu'un client en ayant eu
    deux sur vingt, et c'est cette proportion qui porte le signal.

    A noter, et c'est contre-intuitif : la moyenne des parts par dossier
    (11,1 % de refus) est bien inferieure au taux global mesure sur les lignes
    (17,4 %). Les deux sont justes, ils ne ponderent pas la meme chose — l'un
    donne une voix a chaque client, l'autre a chaque demande.

    L'ecart s'explique par une relation tres nette, mesuree le 28/08/2026 :

        1 demande     ->  0,5 % de refus     ( 60 458 clients)
        2 demandes    ->  6,0 %              ( 52 737 clients)
        3 a 5         -> 10,8 %              (115 011 clients)
        6 a 10        -> 17,0 %              ( 79 579 clients)
        11 et plus    -> 26,9 %              ( 31 072 clients)

    Les clients qui reviennent souvent sont ceux qui essuient le plus de refus.
    Ils pesent lourd dans le taux global, mais comptent pour un dans la moyenne
    par dossier. PREV_NB_DEMANDES est donc, a lui seul, une variable
    predictive — et c'est pour cela qu'on conserve le nombre ET la part.
    """
    agregations = []
    for valeur, nom in correspondances.items():
        agregations.append(
            F.avg(F.when(F.col(colonne) == valeur, 1.0).otherwise(0.0)).alias(
                f"{prefixe}_PART_{nom}"
            )
        )
    return agregations


def agreger(demandes):
    """Une ligne par dossier, a partir des demandes anterieures."""

    # Part de ce qui a ete reellement accorde par rapport a ce qui etait
    # demande. Inferieur a 1 quand l'etablissement a accorde moins que le
    # montant sollicite — un signe qu'il avait des reserves.
    #
    # Le denominateur peut valoir zero (demande a montant nul, cas rare mais
    # present), d'ou le garde-fou.
    demandes = demandes.withColumn(
        "part_accordee",
        F.when(
            F.col("AMT_APPLICATION") > 0,
            F.col("AMT_CREDIT") / F.col("AMT_APPLICATION"),
        ),
    )

    agregations = [
        # Volume de la relation.
        F.count("*").alias("PREV_NB_DEMANDES"),
        F.sum(F.when(F.col("NAME_CONTRACT_STATUS") == "Refused", 1).otherwise(0)).alias(
            "PREV_NB_REFUSEES"
        ),
        # Ecart entre demande et accorde.
        F.avg("part_accordee").alias("PREV_PART_ACCORDEE_MEAN"),
        F.min("part_accordee").alias("PREV_PART_ACCORDEE_MIN"),
        # Montants sollicites et accordes.
        F.avg("AMT_APPLICATION").alias("PREV_AMT_APPLICATION_MEAN"),
        F.max("AMT_APPLICATION").alias("PREV_AMT_APPLICATION_MAX"),
        F.avg("AMT_CREDIT").alias("PREV_AMT_CREDIT_MEAN"),
        F.max("AMT_CREDIT").alias("PREV_AMT_CREDIT_MAX"),
        F.sum("AMT_CREDIT").alias("PREV_AMT_CREDIT_SUM"),
        F.avg("AMT_ANNUITY").alias("PREV_AMT_ANNUITY_MEAN"),
        F.max("AMT_ANNUITY").alias("PREV_AMT_ANNUITY_MAX"),
        F.avg("AMT_GOODS_PRICE").alias("PREV_AMT_GOODS_PRICE_MEAN"),
        # Apport personnel. Absent dans la moitie des cas : le NULL est
        # conserve, il signifie "pas d'apport enregistre", pas "apport nul".
        F.avg("AMT_DOWN_PAYMENT").alias("PREV_AMT_DOWN_PAYMENT_MEAN"),
        F.avg("RATE_DOWN_PAYMENT").alias("PREV_RATE_DOWN_PAYMENT_MEAN"),
        # Duree des credits accordes, en nombre de mensualites.
        F.avg("CNT_PAYMENT").alias("PREV_CNT_PAYMENT_MEAN"),
        F.max("CNT_PAYMENT").alias("PREV_CNT_PAYMENT_MAX"),
        # Anciennete de la relation. Les jours sont negatifs : le MAXIMUM est
        # la demande la plus recente, le MINIMUM la plus ancienne.
        F.max("DAYS_DECISION").alias("PREV_JOURS_DERNIERE_DEMANDE"),
        F.min("DAYS_DECISION").alias("PREV_JOURS_PREMIERE_DEMANDE"),
        # Assurance souscrite a l'acceptation.
        F.avg("NFLAG_INSURED_ON_APPROVAL").alias("PREV_PART_ASSUREES"),
    ]
    agregations += part_par_categorie("NAME_CONTRACT_STATUS", ISSUES, "PREV")
    agregations += part_par_categorie("NAME_CONTRACT_TYPE", TYPES_CONTRAT, "PREV")

    dossiers = demandes.groupBy("SK_ID_CURR").agg(*agregations)

    # Duree totale de la relation, en jours. Un client suivi depuis longtemps
    # est mieux connu qu'un nouveau venu.
    dossiers = dossiers.withColumn(
        "PREV_DUREE_RELATION_JOURS",
        F.col("PREV_JOURS_DERNIERE_DEMANDE") - F.col("PREV_JOURS_PREMIERE_DEMANDE"),
    )

    dossiers = dossiers.withColumn("PREV_PRESENT", F.lit(1))
    return dossiers


def main():
    echantillon = int(os.environ.get("CREDISCORE_ECHANTILLON", "0"))
    memoire = int(os.environ.get("CREDISCORE_MEMOIRE_GO", "4"))

    spark = commun.creer_session("agreger-previous", memoire_go=memoire)

    print("Lecture de previous_application.csv")
    demandes = commun.lire_source(
        spark, "previous_application.csv", echantillon=echantillon
    )
    nb_demandes = demandes.count()
    print(f"  {nb_demandes:,} demandes anterieures".replace(",", " "))

    dossiers = agreger(demandes)
    dossiers.cache()
    nb_dossiers = dossiers.count()
    print(f"  {nb_dossiers:,} dossiers".replace(",", " "))

    # Meme controle que pour installments : la sortie doit avoir exactement
    # une ligne par dossier, sans quoi la jointure au socle dupliquerait.
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

    variables = [c for c in dossiers.columns if c.startswith("PREV_")]
    print(f"  {len(variables)} variables produites")

    print()
    print("Repartition moyenne des issues, tous dossiers confondus :")
    parts = dossiers.select(
        F.round(F.avg("PREV_PART_ACCEPTEES") * 100, 1).alias("acceptees"),
        F.round(F.avg("PREV_PART_REFUSEES") * 100, 1).alias("refusees"),
        F.round(F.avg("PREV_PART_ANNULEES") * 100, 1).alias("annulees"),
        F.round(F.avg("PREV_PART_NON_UTILISEES") * 100, 1).alias("non_utilisees"),
    ).collect()[0]
    print(f"  acceptees     : {parts['acceptees']} %")
    print(f"  refusees      : {parts['refusees']} %")
    print(f"  annulees      : {parts['annulees']} %")
    print(f"  non utilisees : {parts['non_utilisees']} %")

    destination = commun.ecrire(dossiers, "curated", "previous_agrege")
    print()
    print(f"Ecrit dans {destination}")

    spark.stop()


if __name__ == "__main__":
    main()
