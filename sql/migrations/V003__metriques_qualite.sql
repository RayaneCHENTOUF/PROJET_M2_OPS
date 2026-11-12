-- V003 : metriques publiees par les controles de qualite (TD 4, etape 4).
-- Une ligne par mesure : (moment du controle, nom de la metrique, source).
-- source = '*' pour une valeur globale (toutes sources confondues).
-- Serie temporelle interrogeable en SQL : evolution du taux de rejet, controles en echec...
-- SQL portable MySQL 8 / SQLite.

CREATE TABLE metriques_qualite (
    mesure_le     DATETIME      NOT NULL,
    nom           VARCHAR(50)   NOT NULL,
    source        VARCHAR(100)  NOT NULL,
    valeur        DOUBLE        NOT NULL,
    PRIMARY KEY (mesure_le, nom, source)
);

CREATE INDEX idx_metriques_nom ON metriques_qualite (nom, mesure_le)
