"""Assemble le socle des demandes et les six tables agregees.

C'est l'etape qui produit la table finale du pipeline : une ligne par dossier,
avec toutes les variables dont le modele a besoin.

Elle fait quatre choses, dans cet ordre :

  1. DEVIER LES ATTRIBUTS SENSIBLES vers une table separee. C'est l'etape la
     plus importante du bloc, et elle vient en premier : apres elle, le genre
     et l'age n'existent tout simplement plus dans le flux principal. La
     non-discrimination cesse d'etre une regle a respecter pour devenir un fait.

  2. Neutraliser la sentinelle DAYS_EMPLOYED = 365243, qui vaut mille ans
     d'anciennete professionnelle chez 55 374 dossiers.

  3. Calculer les sept ratios metier, en protegeant chaque denominateur.

  4. Joindre les six tables agregees, en jointure GAUCHE : un dossier sans
     historique garde des NULL, jamais des zeros (regle F5).

Entree : raw/application_train.csv + application_test.csv
         curated/*_agrege (six tables)
Sortie : curated/socle_complet      -> 356 255 dossiers, sans attribut sensible
         clean/attributs_sensibles  -> les colonnes protegees, a part
"""

import os
import sys

from pyspark.sql import functions as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commun

# LES ATTRIBUTS PROTEGES.
#
# Cette liste est le miroir de configs/sensitive_features.yaml du depot
# crediscore-ml, ou elle est deja contrainte par le controle C-1 en integration
# continue. Elle est reprise ici parce que le pipeline doit l'appliquer sans
# dependre de l'autre depot.
#
# Ces colonnes quittent le flux principal AVANT tout calcul de variable. Le
# modele ne peut donc pas les utiliser, meme par accident : elles n'existent
# plus dans la table qu'il lira.
ATTRIBUTS_SENSIBLES = ["CODE_GENDER", "DAYS_BIRTH", "NAME_FAMILY_STATUS"]

# Sentinelle des retraites et sans-emploi : 365243 jours, soit mille ans.
SENTINELLE_EMPLOI = 365243

# Les six tables produites par les jobs d'agregation, et leur indicateur de
# presence. Apres jointure gauche, un dossier absent d'une table aura NULL
# partout ; l'indicateur passe alors a 0 pour distinguer "pas d'historique" de
# "historique sans incident".
TABLES_AGREGEES = {
    "installments_agrege": "INSTAL_PRESENT",
    "previous_agrege": "PREV_PRESENT",
    "pos_cash_agrege": "POS_PRESENT",
    "bureau_agrege": "BUREAU_PRESENT",
    "bureau_balance_agrege": "BB_PRESENT",
    "credit_card_agrege": "CC_PRESENT",
}

# 307 511 dossiers annotes + 48 744 a scorer.
DOSSIERS_ATTENDUS = 356_255


def charger_socle(spark, echantillon):
    """Reunit les demandes annotees et celles a scorer en une seule table.

    Les deux fichiers ont les memes colonnes a une exception pres : TARGET
    n'existe que dans le premier, puisque c'est ce qu'on cherche a predire. On
    l'ajoute a vide dans le second pour pouvoir empiler les deux.
    """
    train = commun.lire_source(spark, "application_train.csv", echantillon=echantillon)
    test = commun.lire_source(spark, "application_test.csv", echantillon=echantillon)

    test = test.withColumn("TARGET", F.lit(None).cast("int"))
    # Le marqueur permet de retrouver les deux populations apres l'assemblage.
    train = train.withColumn("EST_ANNOTE", F.lit(1))
    test = test.withColumn("EST_ANNOTE", F.lit(0))

    # unionByName plutot que union : l'ordre des colonnes differe entre les
    # deux fichiers, et un union positionnel melangerait les valeurs sans
    # prevenir.
    return train.unionByName(test)


def devier_attributs_sensibles(socle):
    """Extrait les attributs proteges, puis les retire du flux principal."""

    audit = socle.select(
        "SK_ID_CURR",
        F.col("CODE_GENDER").alias("genre"),
        # L'age en annees, plus lisible qu'un nombre de jours negatif, pour les
        # sous-populations des tests d'equite.
        F.round(-F.col("DAYS_BIRTH") / 365.25, 1).alias("age_annees"),
        F.col("NAME_FAMILY_STATUS").alias("situation_familiale"),
    )

    socle = socle.drop(*ATTRIBUTS_SENSIBLES)
    return socle, audit


def neutraliser_sentinelle_emploi(socle):
    """365243 jours d'anciennete n'est pas une duree, c'est un code d'absence."""

    socle = socle.withColumn(
        "DAYS_EMPLOYED_ANORMAL",
        F.when(F.col("DAYS_EMPLOYED") == SENTINELLE_EMPLOI, 1).otherwise(0),
    )
    return socle.withColumn(
        "DAYS_EMPLOYED",
        F.when(F.col("DAYS_EMPLOYED") != SENTINELLE_EMPLOI, F.col("DAYS_EMPLOYED")),
    )


def calculer_ratios(socle):
    """Les sept ratios metier, avec chaque denominateur protege.

    Les montants bruts disent peu ; leurs rapports disent la capacite de
    remboursement. Chaque denominateur peut valoir zero ou manquer — l'analyse
    exploratoire l'a mesure — donc aucune division n'est faite a l'aveugle.
    Sans ces garde-fous, des valeurs infinies entreraient sans bruit dans le
    feature store.
    """
    revenu = F.col("AMT_INCOME_TOTAL")
    credit = F.col("AMT_CREDIT")

    socle = socle.withColumn(
        "RATIO_CREDIT_REVENU", F.when(revenu > 0, credit / revenu)
    )
    # Le taux d'effort mensuel : le ratio reglementaire de reference du credit
    # a la consommation. Sa presence dans une explication de refus est
    # immediatement comprehensible par le demandeur.
    socle = socle.withColumn(
        "RATIO_ANNUITE_REVENU", F.when(revenu > 0, F.col("AMT_ANNUITY") / revenu)
    )
    socle = socle.withColumn(
        "RATIO_CREDIT_BIEN",
        F.when(F.col("AMT_GOODS_PRICE") > 0, credit / F.col("AMT_GOODS_PRICE")),
    )
    socle = socle.withColumn(
        "RATIO_ANNUITE_CREDIT", F.when(credit > 0, F.col("AMT_ANNUITY") / credit)
    )
    socle = socle.withColumn(
        "REVENU_PAR_PERSONNE",
        F.when(F.col("CNT_FAM_MEMBERS") > 0, revenu / F.col("CNT_FAM_MEMBERS")),
    )
    # Anciennete professionnelle rapportee a l'anciennete du dossier, et NON a
    # l'age : le ratio classique anciennete/age derive d'une variable sensible,
    # ce que la regle F2 interdit.
    socle = socle.withColumn(
        "RATIO_ANCIENNETE",
        F.when(
            F.col("DAYS_REGISTRATION") != 0,
            F.col("DAYS_EMPLOYED") / F.col("DAYS_REGISTRATION"),
        ),
    )

    # Completude du dossier. La mesure a montre que seul FLAG_DOCUMENT_3 porte
    # du signal ; les dix-neuf autres sont quasi constants ou sans lien avec le
    # defaut. On garde donc le premier isolement, et la somme des autres.
    autres = [
        c
        for c in socle.columns
        if c.startswith("FLAG_DOCUMENT_") and c != "FLAG_DOCUMENT_3"
    ]
    somme = F.lit(0)
    for colonne in autres:
        somme = somme + F.coalesce(F.col(colonne), F.lit(0))
    socle = socle.withColumn("NB_DOCUMENTS", somme)

    # Les dix-neuf indicateurs sont retires une fois leur somme calculee :
    # vingt colonnes deviennent deux variables, sans perte de signal.
    return socle.drop(*autres)


def joindre_agregats(spark, socle):
    """Jointure GAUCHE des six tables agregees."""

    for table, indicateur in TABLES_AGREGEES.items():
        agregat = commun.lire(spark, "curated", table)
        socle = socle.join(agregat, on="SK_ID_CURR", how="left")
        # L'indicateur vaut 1 quand la table apportait une ligne, 0 sinon. Il
        # distingue "aucun historique" de "historique sans incident", que les
        # NULL seuls confondraient.
        socle = socle.withColumn(
            indicateur, F.coalesce(F.col(indicateur), F.lit(0))
        )
    return socle


def main():
    echantillon = int(os.environ.get("CREDISCORE_ECHANTILLON", "0"))
    memoire = int(os.environ.get("CREDISCORE_MEMOIRE_GO", "4"))

    spark = commun.creer_session("construire-socle", memoire_go=memoire)

    print("Lecture des demandes")
    socle = charger_socle(spark, echantillon)
    nb_socle = socle.count()
    print(f"  {nb_socle:,} demandes".replace(",", " "))

    print("Deviation des attributs sensibles")
    socle, audit = devier_attributs_sensibles(socle)
    print(f"  {len(ATTRIBUTS_SENSIBLES)} colonnes retirees du flux principal")

    socle = neutraliser_sentinelle_emploi(socle)
    nb_sentinelles = socle.filter(F.col("DAYS_EMPLOYED_ANORMAL") == 1).count()
    print(f"  {nb_sentinelles:,} sentinelles d'emploi neutralisees".replace(",", " "))

    print("Calcul des sept ratios metier")
    socle = calculer_ratios(socle)

    print("Jointure des six tables agregees")
    socle = joindre_agregats(spark, socle)
    socle.cache()

    nb_final = socle.count()
    print(f"  {nb_final:,} lignes, {len(socle.columns)} colonnes".replace(",", " "))

    # CONTROLE 1 : la jointure n'a pas duplique de dossier.
    nb_distincts = socle.select("SK_ID_CURR").distinct().count()
    if nb_distincts != nb_final:
        raise SystemExit(
            f"ECHEC : {nb_final} lignes pour {nb_distincts} dossiers distincts. "
            f"Une jointure a duplique des dossiers."
        )
    print("  Verification OK : une seule ligne par dossier")

    # CONTROLE 2 : aucun dossier perdu.
    if echantillon == 0 and nb_final != DOSSIERS_ATTENDUS:
        raise SystemExit(
            f"ECHEC : {nb_final} dossiers au lieu des {DOSSIERS_ATTENDUS} attendus."
        )

    # CONTROLE 3 : aucun attribut sensible n'a survecu. C'est le controle C-1,
    # applique cette fois a la sortie du pipeline et non au code du modele.
    survivants = [c for c in socle.columns if c in ATTRIBUTS_SENSIBLES]
    if survivants:
        raise SystemExit(
            f"ECHEC : attributs sensibles presents dans la sortie : {survivants}"
        )
    print("  Verification OK : aucun attribut sensible dans le socle")

    prefixes = ("INSTAL_", "PREV_", "POS_", "BUREAU_", "BB_", "CC_", "RATIO_")
    variables = [c for c in socle.columns if c.startswith(prefixes)]
    print(f"  {len(variables)} variables construites par le pipeline")

    print()
    print("Couverture des six sources :")
    couvertures = socle.select(
        *[
            F.round(F.avg(indicateur) * 100, 1).alias(table.replace("_agrege", ""))
            for table, indicateur in TABLES_AGREGEES.items()
        ]
    ).collect()[0]
    for table in TABLES_AGREGEES:
        nom = table.replace("_agrege", "")
        print(f"  {nom:<16} {couvertures[nom]:>5} %")

    commun.ecrire(socle, "curated", "socle_complet")

    # Les attributs sensibles n'atterrissent PAS dans curated/, ou le modele
    # lit ses variables : les y mettre reviendrait a defaire la deviation qu'on
    # vient d'operer. Ils vont dans clean/, et leur destination finale est le
    # schema audit_equite de PostgreSQL, dont les droits sont distincts.
    commun.ecrire(audit, "clean", "attributs_sensibles")

    print()
    print("Ecrit dans curated/socle_complet")
    print("Attributs sensibles ecrits a part dans clean/attributs_sensibles")

    spark.stop()


if __name__ == "__main__":
    main()
