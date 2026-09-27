-- =============================================================================
-- CrediScore — Modèle physique : entrepôt en étoile, feature store, audit
-- Modélisation et sécurisation par la séparation des accès
-- =============================================================================
--
-- Quatre schémas, et la séparation entre eux EST une mesure de sécurité :
--
--   entrepot      schéma en étoile — reporting risques et suivi du portefeuille
--   feature_store variables servies à l'entraînement ET au scoring
--   audit_equite  attributs sensibles, isolés, jamais joints au modèle
--   journal       piste d'audit des décisions (article 22 RGPD)
--
-- La règle qui commande le reste : les attributs sensibles (genre, âge) vivent
-- dans un schéma distinct, avec des droits distincts. Quelqu'un qui construit
-- des variables ne peut pas les atteindre par inadvertance — la
-- non-discrimination devient une propriété de l'infrastructure, et non une
-- promesse écrite dans un document.

CREATE SCHEMA IF NOT EXISTS entrepot;
CREATE SCHEMA IF NOT EXISTS feature_store;
CREATE SCHEMA IF NOT EXISTS audit_equite;
CREATE SCHEMA IF NOT EXISTS journal;

COMMENT ON SCHEMA entrepot      IS 'Schéma en étoile : reporting risques et pilotage du portefeuille.';
COMMENT ON SCHEMA feature_store IS 'Variables servies à l entraînement et au scoring — définitions identiques des deux côtés.';
COMMENT ON SCHEMA audit_equite  IS 'Attributs sensibles isolés. Usage exclusif : tests de non-discrimination.';
COMMENT ON SCHEMA journal       IS 'Piste d audit des décisions de scoring (article 22 RGPD).';

-- =============================================================================
-- 1. DIMENSIONS
-- =============================================================================

-- Dimension temps : indispensable pour analyser la saisonnalité de la demande
-- et surveiller la dérive du modèle dans le temps.
CREATE TABLE IF NOT EXISTS entrepot.dim_temps (
    id_temps        INTEGER PRIMARY KEY,          -- AAAAMMJJ, clé lisible
    date_complete   DATE        NOT NULL UNIQUE,
    annee           SMALLINT    NOT NULL,
    trimestre       SMALLINT    NOT NULL CHECK (trimestre BETWEEN 1 AND 4),
    mois            SMALLINT    NOT NULL CHECK (mois BETWEEN 1 AND 12),
    numero_semaine  SMALLINT    NOT NULL,
    jour_du_mois    SMALLINT    NOT NULL,
    jour_semaine    SMALLINT    NOT NULL,         -- 1 = lundi
    est_week_end    BOOLEAN     NOT NULL
);

-- Dimension demandeur — SANS aucun attribut sensible : ni genre, ni date de
-- naissance. Ces colonnes existent dans la source ; elles sont déviées vers
-- audit_equite dès l'ingestion (contrôle C-1).
CREATE TABLE IF NOT EXISTS entrepot.dim_demandeur (
    id_demandeur            BIGSERIAL PRIMARY KEY,
    sk_id_curr              BIGINT      NOT NULL UNIQUE,   -- clé naturelle du dossier
    type_revenu             TEXT,
    niveau_etudes           TEXT,
    statut_logement         TEXT,
    profession              TEXT,
    nb_enfants              SMALLINT,
    taille_famille          SMALLINT,
    anciennete_emploi_jours INTEGER,
    anciennete_client_jours INTEGER,
    possede_voiture         BOOLEAN,
    possede_immobilier      BOOLEAN
);

COMMENT ON COLUMN entrepot.dim_demandeur.anciennete_emploi_jours IS
    'DAYS_EMPLOYED nettoyé : la sentinelle 365243 (retraités) est mise à NULL — plan_features.md section 2.3.';

-- Dimension produit : le crédit demandé, pas le client.
CREATE TABLE IF NOT EXISTS entrepot.dim_produit (
    id_produit      BIGSERIAL PRIMARY KEY,
    type_contrat    TEXT NOT NULL,      -- Cash loans / Revolving loans
    tranche_montant TEXT NOT NULL,      -- discrétisation pour le reporting
    UNIQUE (type_contrat, tranche_montant)
);

-- Dimension bureau agrégé : photo de l'exposition du demandeur chez les autres
-- établissements, précalculée par le pipeline. Modélisée en dimension plutôt que
-- recalculée à la volée : c'est ce qui tient la contrainte de quelques secondes
-- au point de vente.
CREATE TABLE IF NOT EXISTS entrepot.dim_bureau_agrege (
    id_bureau_agrege     BIGSERIAL PRIMARY KEY,
    sk_id_curr           BIGINT NOT NULL UNIQUE,
    nb_credits_total     INTEGER,
    nb_credits_actifs    INTEGER,
    nb_credits_clos      INTEGER,
    montant_du_total     NUMERIC(18, 2),
    nb_incidents         INTEGER,
    anciennete_max_jours INTEGER
);

-- =============================================================================
-- 2. TABLE DE FAITS
-- =============================================================================
-- Grain : une demande de crédit. Une ligne = une décision d'octroi possible.

CREATE TABLE IF NOT EXISTS entrepot.fait_demande (
    id_demande          BIGSERIAL PRIMARY KEY,
    sk_id_curr          BIGINT      NOT NULL UNIQUE,

    id_demandeur        BIGINT      REFERENCES entrepot.dim_demandeur (id_demandeur),
    id_produit          BIGINT      REFERENCES entrepot.dim_produit (id_produit),
    id_temps            INTEGER     REFERENCES entrepot.dim_temps (id_temps),
    id_bureau_agrege    BIGINT      REFERENCES entrepot.dim_bureau_agrege (id_bureau_agrege),

    -- Mesures brutes
    montant_credit      NUMERIC(18, 2),
    montant_annuite     NUMERIC(18, 2),
    montant_bien        NUMERIC(18, 2),
    revenu_total        NUMERIC(18, 2),

    -- Ratios métier calculés une fois et réutilisés (plan_features section 2.4)
    ratio_credit_revenu   NUMERIC(12, 4),
    ratio_annuite_revenu  NUMERIC(12, 4),
    ratio_bien_credit     NUMERIC(12, 4),

    -- Cible : connue seulement sur l'historique annoté
    cible_defaut        SMALLINT CHECK (cible_defaut IN (0, 1)),

    -- Sortie du modèle, renseignée au scoring
    probabilite_defaut  NUMERIC(6, 5) CHECK (probabilite_defaut BETWEEN 0 AND 1),
    seuil_applique      NUMERIC(6, 5),
    decision            TEXT CHECK (decision IN ('accorde', 'refuse', 'revue_humaine')),
    version_modele      TEXT
);

CREATE INDEX IF NOT EXISTS idx_fait_demande_temps ON entrepot.fait_demande (id_temps);
CREATE INDEX IF NOT EXISTS idx_fait_demande_cible ON entrepot.fait_demande (cible_defaut);

COMMENT ON TABLE entrepot.fait_demande IS
    'Table de faits, grain = une demande de crédit. Relie les quatre dimensions et porte mesures, cible et décision.';

-- =============================================================================
-- 3. FEATURE STORE
-- =============================================================================
-- La table large des quelque 230 variables est créée par le pipeline à
-- partir du manifeste : l'énumérer en DDL figerait un contrat qui doit évoluer
-- avec le feature engineering. Son REGISTRE, lui, est défini ici — c'est lui qui
-- rend chaque variable traçable jusqu'à sa source.

CREATE TABLE IF NOT EXISTS feature_store.registre_variables (
    nom_variable    TEXT PRIMARY KEY,
    source          TEXT NOT NULL,      -- application, PREV, BUREAU, INSTAL, POS, CC, BB
    agregat         TEXT,               -- mean, max, sum, std, count…
    type_donnee     TEXT NOT NULL,
    description     TEXT NOT NULL,
    est_sensible    BOOLEAN NOT NULL DEFAULT FALSE,
    date_ajout      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Gouvernance rendue exécutable : aucune variable marquée sensible ne peut
    -- entrer au registre des variables servies au modèle. La base refuse
    -- l'insertion — le contrôle n'est pas contournable par oubli.
    CONSTRAINT aucune_variable_sensible_servie CHECK (est_sensible = FALSE)
);

COMMENT ON TABLE feature_store.registre_variables IS
    'Une ligne par variable servie. Permet de remonter d un facteur SHAP à sa définition sans lire le code, et matérialise la minimisation RGPD : ce qui n est pas au registre n est pas publié.';

-- Traçabilité des publications : quel run a publié quoi, à partir de quelles
-- données. C'est la pièce qui rend la piste d'audit reproductible.
CREATE TABLE IF NOT EXISTS feature_store.journal_publication (
    id_publication      BIGSERIAL PRIMARY KEY,
    horodatage          TIMESTAMPTZ NOT NULL DEFAULT now(),
    identifiant_run     TEXT NOT NULL,          -- run_id Airflow
    version_pipeline    TEXT NOT NULL,          -- SHA du commit
    nb_dossiers         INTEGER NOT NULL,
    nb_variables        INTEGER NOT NULL,
    empreinte_donnees   TEXT,                   -- hachage du lot source
    controles_reussis   BOOLEAN NOT NULL,
    details             JSONB
);

-- =============================================================================
-- 4. ATTRIBUTS SENSIBLES — ISOLÉS
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_equite.attributs_sensibles (
    sk_id_curr      BIGINT PRIMARY KEY,
    genre           TEXT,
    age_annees      SMALLINT,
    tranche_age     TEXT,
    date_extraction TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE audit_equite.attributs_sensibles IS
    'Attributs sensibles isolés dès l ingestion. Usage EXCLUSIF : mesurer la parité démographique et l égalité des chances. Toute jointure avec feature_store dans du code de modélisation est une non-conformité (AI Act, contrôle C-1).';

-- =============================================================================
-- 5. PISTE D'AUDIT DES DÉCISIONS (article 22 RGPD)
-- =============================================================================

CREATE TABLE IF NOT EXISTS journal.decisions (
    id_decision          BIGSERIAL PRIMARY KEY,
    horodatage           TIMESTAMPTZ NOT NULL DEFAULT now(),
    sk_id_curr           BIGINT NOT NULL,
    probabilite_defaut   NUMERIC(6, 5) NOT NULL,
    seuil_applique       NUMERIC(6, 5) NOT NULL,
    decision             TEXT NOT NULL CHECK (decision IN ('accorde', 'refuse', 'revue_humaine')),
    facteurs_shap        JSONB NOT NULL,     -- facteurs ayant motivé la décision
    version_modele       TEXT NOT NULL,
    duree_ms             INTEGER,
    revue_humaine        BOOLEAN NOT NULL DEFAULT FALSE,
    identifiant_analyste TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_dossier ON journal.decisions (sk_id_curr);
CREATE INDEX IF NOT EXISTS idx_decisions_date    ON journal.decisions (horodatage);

COMMENT ON TABLE journal.decisions IS
    'Une ligne par décision rendue, avec ses facteurs explicatifs. Permet de répondre à une contestation : quel score, quel seuil, quels facteurs, quelle version du modèle, et si un analyste est intervenu.';
