-- V002 : sortie du traitement qualite (TD 4).
-- Chaque article brut finit dans UNE des deux tables, jamais les deux, jamais deux fois :
-- l'id est celui du brut (sha256(source, lien)), ce qui donne la tracabilite
-- article propre -> fichier brut (chemin_brut) -> source (sources.nom).
-- SQL portable MySQL 8 / SQLite.

CREATE TABLE articles_propres (
    id            VARCHAR(32)   NOT NULL PRIMARY KEY,
    source        VARCHAR(100)  NOT NULL,
    lien          VARCHAR(1000) NOT NULL,
    titre         VARCHAR(500)  NOT NULL,
    cle_titre     VARCHAR(500)  NOT NULL,
    extrait       VARCHAR(400)  NULL,
    publie_le     DATETIME      NULL,
    recupere_le   DATETIME      NOT NULL,
    rubrique      VARCHAR(200)  NULL,
    chemin_brut   VARCHAR(500)  NOT NULL,
    passage       VARCHAR(20)   NOT NULL,
    traite_le     DATETIME      NOT NULL
);

CREATE INDEX idx_propres_recupere ON articles_propres (recupere_le);
CREATE INDEX idx_propres_passage ON articles_propres (passage);

CREATE TABLE rejets (
    id            VARCHAR(32)   NOT NULL PRIMARY KEY,
    source        VARCHAR(100)  NOT NULL,
    titre         VARCHAR(500)  NOT NULL,
    regle         VARCHAR(50)   NOT NULL,
    detail        VARCHAR(500)  NULL,
    chemin_brut   VARCHAR(500)  NOT NULL,
    passage       VARCHAR(20)   NOT NULL,
    traite_le     DATETIME      NOT NULL
);

CREATE INDEX idx_rejets_passage ON rejets (passage);
CREATE INDEX idx_rejets_regle ON rejets (regle)
