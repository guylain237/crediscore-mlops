"""Remplace les identifiants de dossier par un pseudonyme dans les journaux.

Pourquoi c'est utile ici :

Les donnees sont dans S3, protegees par IAM. Les JOURNAUX Airflow, eux, sont
sur le disque de la VM et dans l'interface web. Quiconque a le mot de passe
Airflow peut les lire.

Ecrire "dossier 100013 rejete" dans un journal fait donc sortir un identifiant
de la zone protegee vers une zone qui l'est beaucoup moins.

On le remplace par une empreinte. Le meme dossier donne toujours le meme
pseudonyme, ce qui permet de suivre un incident. Mais sans le sel, on ne peut
pas revenir a l'identifiant d'origine.

Controle C-7 du plan de gouvernance.
"""

import hashlib
import os

# Le sel rend le pseudonyme impossible a deviner. Sans lui, il suffirait de
# hacher tous les identifiants possibles pour retrouver la correspondance :
# ils vont de 100001 a environ 456255, ce qui se teste en quelques secondes.
#
# En production, le sel vient du fichier .env de la VM. La valeur de repli sert
# au developpement et ne protege rien — c'est ecrit pour qu'on ne s'y trompe pas.
SEL = os.environ.get("CREDISCORE_SEL_PSEUDO", "sel-de-developpement")

# Douze caracteres suffisent : assez pour eviter les collisions sur 356 255
# dossiers, assez court pour rester lisible dans un journal.
LONGUEUR = 12


def pseudonymiser(identifiant):
    """Transforme un identifiant de dossier en pseudonyme."""
    texte = SEL + str(identifiant)
    return hashlib.sha256(texte.encode()).hexdigest()[:LONGUEUR]


def ajouter_pseudonyme(df, colonne="SK_ID_CURR"):
    """Ajoute une colonne PSEUDO a un DataFrame Spark.

    Sert a afficher un apercu dans les journaux sans y mettre de vrai
    identifiant.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    calculer = F.udf(pseudonymiser, StringType())
    return df.withColumn("PSEUDO", calculer(F.col(colonne)))
