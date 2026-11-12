CREATE TABLE IF NOT EXISTS articles (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    source      VARCHAR(100),
    titre       TEXT,
    url         VARCHAR(500),
    publie_le   DATETIME,
    collecte_le DATETIME DEFAULT CURRENT_TIMESTAMP
);
 