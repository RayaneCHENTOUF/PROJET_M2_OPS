-- V001 : table des sources suivies par le collecteur.
-- SQL portable MySQL 8 / SQLite (les tests tournent sur SQLite).
-- Ne jamais modifier une migration deja appliquee : en ajouter une nouvelle (V002__...).

CREATE TABLE sources (
    nom                 VARCHAR(100) NOT NULL PRIMARY KEY,
    type                VARCHAR(20)  NOT NULL,
    url_flux            VARCHAR(500) NOT NULL,
    actif               BOOLEAN      NOT NULL DEFAULT TRUE,
    derniere_reussite   DATETIME     NULL,
    echecs_consecutifs  INT          NOT NULL DEFAULT 0,
    etag                VARCHAR(255) NULL,
    last_modified       VARCHAR(64)  NULL,
    derniere_erreur     VARCHAR(500) NULL
);
