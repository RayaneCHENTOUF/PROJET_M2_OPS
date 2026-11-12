# Dictionnaire et contrat des données

La collecte produit des **fichiers bruts** (un par article) et une **table `sources`** qui dit où en est chaque flux (§ 1 à 4). Le traitement qualité en tire les **articles propres**, les **rejets**, des **métriques** et un **rapport du jour** (§ 5 à 8). Le contrat porte sur la fraîcheur, la rétention, ce qu'on ne stocke pas et la traçabilité (§ 9 à 12).

## 1. Articles bruts — `data/articles_bruts/`

```
data/articles_bruts/
└── 2026-11-12/                 date de récupération (UTC)
    ├── le-monde/
    │   ├── 16fd27b0c7fec242ed8ef5b4723cf7c6.json
    │   └── ...
    └── ouest-france/
        └── ...
```

Un fichier JSON par article, écrit de façon atomique (fichier temporaire puis renommage). Le dossier `data/` n'est pas versionné.

| Champ         | Type                  | Description |
|---------------|-----------------------|-------------|
| `id`          | texte (32 hexa)       | `sha256(source + "\n" + lien)`, tronqué. Dérivé de la clé naturelle, donc stable d'un passage à l'autre. C'est aussi le nom du fichier. |
| `source`      | texte                 | Nom de la source, identique à `sources.nom` (ex. `Le Monde`). |
| `lien`        | URL                   | Lien de l'article (`<link>` en RSS, `<link rel="alternate">` en Atom ; à défaut le `guid`/`id` s'il est une URL). Le fragment `#...` est retiré. |
| `titre`       | texte                 | `<title>`, espaces de début et de fin retirés. HTML éventuel **non nettoyé** (TD 4). |
| `resume`      | texte, ≤ 1000 car.    | `description` (RSS) ou `summary` (Atom), à défaut le début de `content`. Tronqué : on garde un extrait, **jamais l'article entier**. HTML non nettoyé. |
| `publie_le`   | ISO 8601 UTC ou `null`| `pubDate` (RSS, RFC 822) ou `published`/`updated` (Atom, ISO 8601), converti en UTC. `null` si le flux ne donne pas de date. |
| `recupere_le` | ISO 8601 UTC          | Moment du passage de collecte. Commun à tous les articles d'un même passage et d'une même source. |
| `rubrique`    | texte ou `null`       | Première `<category>` de l'item, si le flux en fournit une. |
| `payload`     | objet                 | L'item tel que lu par feedparser (dates converties en ISO), **sans** les champs exclus par `CHAMPS_EXCLUS` (`collect/items.py`) : `content`, `summary`, `summary_detail` (texte intégral possible), auteurs, contributeurs et crédits photo (données personnelles), `media_*` (liens vers les images). Voir § 11. |

**Clé naturelle : `(source, lien)`.** Le `guid` aide, mais il n'est pas fiable partout (absent, pas une URL, ou régénéré). Un article déjà écrit pour un `(source, lien)` donné, quel que soit le jour, n'est jamais réécrit : lancer la collecte deux fois ne crée aucun doublon.

**Ce qu'on ne garde pas :** le texte des articles, les commentaires, les images, toute donnée personnelle (§ 11).

## 2. Table `sources`

Créée par la migration versionnée [`sql/migrations/V001__create_sources.sql`](../sql/migrations/V001__create_sources.sql). Alimentée à partir de [`sources/sources.csv`](../sources/sources.csv) par `python -m collect sync-sources`.

| Colonne              | Type          | Description |
|----------------------|---------------|-------------|
| `nom`                | VARCHAR(100) PK | Nom de la source, repris du CSV. |
| `type`               | VARCHAR(20)   | `PQN` (presse nationale) ou `PQR` (presse régionale). |
| `url_flux`           | VARCHAR(500)  | URL du flux RSS ou Atom. |
| `actif`              | BOOLEAN       | Choix éditorial : `0` quand la source est retirée du CSV (la ligne est gardée pour l'historique). |
| `derniere_reussite`  | DATETIME (UTC)| Dernier passage réussi (200 ou 304). |
| `echecs_consecutifs` | INT           | Remis à 0 à chaque réussite. **À 3, la source est en sommeil** : elle n'est plus interrogée tant qu'on ne l'a pas réveillée. |
| `etag`               | VARCHAR(255)  | En-tête `ETag` de la dernière réponse 200, renvoyé en `If-None-Match`. |
| `last_modified`      | VARCHAR(64)   | En-tête `Last-Modified` de la dernière réponse 200, renvoyé en `If-Modified-Since`. |
| `derniere_erreur`    | VARCHAR(500)  | Message du dernier échec (ex. `HTTP 503`, `timeout`), effacé à la réussite suivante. |

`schema_migrations (version, applique_le)` garde la trace des migrations appliquées.

### Règles de mise à jour

- **Sync depuis le CSV** : une nouvelle ligne est insérée. Une URL modifiée réinitialise `etag`, `last_modified` et le compteur. Une source absente du CSV passe à `actif = 0`. L'état de collecte n'est jamais écrasé sinon.
- **Réussite** (200 avec un flux lisible, ou 304) : `derniere_reussite = now`, `echecs_consecutifs = 0`, nouveaux `etag` et `last_modified`.
- **Échec** : `echecs_consecutifs + 1`, `derniere_erreur` renseignée.

## 3. Classement des erreurs

| Cas | Nature | Comportement dans un passage |
|-----|--------|------------------------------|
| 408, 425, 429, 5xx, timeout, connexion refusée | transitoire | 3 essais, attente croissante 2 s puis 4 s (ou `Retry-After`), puis 1 échec |
| 403, 404, 410, autres 4xx | permanente | pas de nouvel essai, 1 échec |
| Réponse 200 qui n'est ni RSS ni Atom | permanente (flux cassé) | pas de nouvel essai, 1 échec |

3 échecs de suite → sommeil. La tâche Airflow `verifier_sommeil` passe alors en rouge. Une fois la cause corrigée : `python -m collect reveiller "Nom"`.

## 4. Journal

Une ligne par source et par passage, puis un bilan :

```
INFO collecte source="Le Monde" statut=ok http=200 recus=16 ecrits=3 ignores=13 duree=0.11s essais=1 echecs_consecutifs=0
ERROR collecte source="Les Échos" statut=echec http=403 recus=0 ecrits=0 ignores=0 duree=0.09s essais=1 echecs_consecutifs=1 erreur="HTTP 403"
INFO collecte bilan sources=10 ok=7 inchange=0 echec=3 sommeil=0 recus=246 ecrits=246 ignores=0
```

`statut` vaut `ok`, `inchange` (304), `echec` ou `sommeil`. `ignores` compte les items déjà connus, les doublons à l'intérieur du flux et les items sans lien.

## 5. Table `articles_propres`

Migration [`V002__articles_propres_rejets.sql`](../sql/migrations/V002__articles_propres_rejets.sql). Alimentée par la tâche Airflow `traiter` (`python -m qualite traiter`) à chaque passage. Elle contient ce que la gazette a le droit de republier, une fois normalisé (`docs/qualite.md` § 4) et passé par les règles (§ 5).

| Colonne       | Type            | Description |
|---------------|-----------------|-------------|
| `id`          | VARCHAR(32) PK  | Identifiant du brut, inchangé : `sha256(source, lien)` tronqué. |
| `source`      | VARCHAR(100)    | Nom de la source (`sources.nom`). |
| `lien`        | VARCHAR(1000)   | Lien vers l'article chez l'éditeur. |
| `titre`       | VARCHAR(500)    | Titre nettoyé : encodage réparé, entités décodées, HTML retiré, espaces normalisées. Préfixes éditoriaux conservés (« VIDÉOS. »). |
| `cle_titre`   | VARCHAR(500)    | Forme de comparaison du titre (sans préfixe, minuscules, sans accents ni ponctuation). Sert au dédoublonnage, **jamais affichée**. |
| `extrait`     | VARCHAR(400)    | Résumé nettoyé, **300 caractères au plus**, coupé sur un mot et suivi de « … ». |
| `publie_le`   | DATETIME (UTC)  | Date de publication. `NULL` si le flux n'en donne pas. |
| `recupere_le` | DATETIME (UTC)  | Moment de la collecte. |
| `rubrique`    | VARCHAR(200)    | Rubrique nettoyée, ou `NULL` (139 articles sur 312 n'en ont pas au 12/11). |
| `chemin_brut` | VARCHAR(500)    | Chemin du fichier brut, relatif à la racine du dépôt (§ 12). |
| `passage`     | VARCHAR(20)     | Passage de traitement qui l'a gardé (`20261112T134612Z`). |
| `traite_le`   | DATETIME (UTC)  | Moment de ce passage. |

**Invariants** (vérifiés par les contrôles de qualité, `docs/qualite.md` § 6) : aucun titre vide, aucune `publie_le` à plus d'1 h dans le futur.

## 6. Table `rejets`

Même migration. Chaque article brut finit dans **une seule** des deux tables, `articles_propres` ou `rejets`, jamais les deux et jamais deux fois : le traitement saute tout `id` déjà présent dans l'une ou l'autre.

| Colonne       | Type            | Description |
|---------------|-----------------|-------------|
| `id`          | VARCHAR(32) PK  | Identifiant du brut. |
| `source`      | VARCHAR(100)    | Nom de la source. |
| `titre`       | VARCHAR(500)    | Titre nettoyé, pour lire le rapport sans rouvrir le brut. |
| `regle`       | VARCHAR(50)     | Règle qui a rejeté l'article (`R01_titre_vide` … `R11_doublon_approx`, `docs/qualite.md` § 5). La première qui rejette l'emporte. |
| `detail`      | VARCHAR(500)    | Pourquoi précisément : motif trouvé, doublon visé (« titre identique déjà publié par L'Est Républicain »). |
| `chemin_brut` | VARCHAR(500)    | Chemin du fichier brut. |
| `passage`, `traite_le` | | Comme dans `articles_propres`. |

## 7. Table `metriques_qualite`

Migration [`V003__metriques_qualite.sql`](../sql/migrations/V003__metriques_qualite.sql). Écrite par la tâche `controler` après chaque passage : une ligne par mesure.

| Colonne     | Type           | Description |
|-------------|----------------|-------------|
| `mesure_le` | DATETIME (UTC) | Moment du contrôle. |
| `nom`       | VARCHAR(50)    | `articles_entres`, `articles_gardes`, `articles_rejetes`, `taux_rejet` (sur 24 h), `dates_futures`, `titres_vides`, `controle_<nom>` (1 = fait vrai, 0 = faux). |
| `source`    | VARCHAR(100)   | Nom de la source, ou `*` pour toutes sources confondues. |
| `valeur`    | DOUBLE         | La mesure. |

Clé primaire : `(mesure_le, nom, source)`.

## 8. Rapports — `rapports/qualite-AAAA-MM-JJ.md`

Un fichier Markdown par jour (UTC), réécrit à chaque passage par la tâche `rapport`. Contenu détaillé dans `docs/qualite.md` § 7.

## 9. Fraîcheur

| Donnée | Mise à jour | Retard maximal attendu |
|---|---|---|
| Fichiers bruts | Toutes les 30 min (DAG `collecte`, `*/30 * * * *`) | 30 min après l'apparition de l'article dans le flux de l'éditeur, plus la durée du passage (moins de 20 min, en pratique quelques secondes) |
| `articles_propres`, `rejets` | Dans le même passage, juste après la collecte (tâche `traiter`) | **≈ 35 min** après l'apparition dans le flux |
| `metriques_qualite`, rapport du jour | Juste après le traitement (`controler`, puis `rapport`) | Quelques secondes après `traiter` |

Le traitement relit les partitions brutes **d'aujourd'hui et d'hier** (`--jours 2`), ce qui suffit à passer minuit. Si l'orchestrateur est arrêté plus longtemps, il faut rattraper à la main : `python -m qualite traiter --jours N`. Le traitement est idempotent, donc on peut toujours relancer.

Signal de fraîcheur dégradée : la tâche `verifier_sommeil` passe en rouge (une source ne répond plus), ou le rapport du jour montre un « dernier passage » ancien.

## 10. Rétention

Appliquée chaque jour à 3 h UTC par le DAG `retention` (`python -m qualite retention`, code : `qualite/retention.py`). `--simulation` affiche ce qui serait fait sans rien toucher.

| Donnée | Durée | Ensuite |
|---|---|---|
| Fichiers bruts (`data/articles_bruts/AAAA-MM-JJ/`) | **30 jours** (date de collecte) | **Archivés** : un `.tar.gz` par jour dans `data/archives/articles_bruts/` |
| Archives des bruts | **1 an** | Supprimées |
| `rejets` | **30 jours** (date de traitement) | Supprimés |
| `articles_propres` | **1 an** (date de collecte) | Supprimés |
| `metriques_qualite` | Gardées | Quelques milliers de lignes par jour : l'historique sert à repérer les dérives (taux de rejet qui monte) |
| `rapports/` | Gardés | Un fichier de quelques Ko par jour : c'est l'archive de la qualité |
| `sources` | Gardée | Une ligne par source, jamais supprimée (`actif = 0` quand elle est retirée) |

**Pourquoi ces durées :**
- **30 jours de bruts actifs** : de quoi rejouer le traitement après un changement de règle sur un mois de données, sans re-collecter (les flux RSS ne gardent que quelques jours).
- **Les bruts sont archivés, pas supprimés,** tant que leur article propre existe. Un article propre est gardé 1 an et doit rester traçable jusqu'à son brut (§ 12). Les archives suivent donc la même durée.
- **30 jours de rejets** : le temps de lire les rapports, de repérer une règle trop gourmande et de l'ajuster (`docs/qualite.md` § 8). Au-delà, ils ne servent plus. Le brut d'un rejet reste de toute façon dans l'archive.
- **1 an d'articles propres** : une revue de presse sert l'actualité. Au-delà d'un an, on ne republie plus ces extraits, et on ne garde pas sans raison des contenus dont on n'est pas l'auteur.
- **Les vieilles éditions** suivent la même logique : 30 jours en ligne dans `data/articles_bruts/`, puis l'archive compressée hors du chemin de travail (le traitement ne lit que les 2 derniers jours), puis la suppression à 1 an. Les éditions publiées de la gazette (étapes suivantes) suivront la durée de `articles_propres`.

**Effet sur le dédoublonnage de la collecte :** la collecte reconnaît un article déjà vu à son fichier dans `data/articles_bruts/` (§ 1). Un article encore présent dans un flux après 30 jours serait donc collecté à nouveau. C'est sans conséquence : le traitement le saute, car son `id` est déjà dans `articles_propres`. Ce cas est rare, car les flux ne gardent que quelques jours d'articles.

## 11. Ce qu'on ne stocke pas

On ne garde que **ce qu'on a le droit de republier** : titre, extrait court, lien, date, source (et la rubrique, pour les règles).

| On ne stocke pas | Comment c'est garanti |
|---|---|
| **Le texte intégral** | `content`, `summary` et `summary_detail` sont exclus du `payload` (`CHAMPS_EXCLUS`). Le résumé brut est tronqué à 1000 caractères, l'extrait publié à 300. |
| **Les données personnelles** | Auteurs (`author`, `authors`, `author_detail`), contributeurs, éditeur et crédits photo (`credit`, `media_credit`) sont exclus du `payload`. Aucun champ ne décrit un lecteur. |
| **Les images** | Ni les fichiers, ni leurs liens (`media_content`, `media_thumbnail` exclus). |
| **Les commentaires** | Les flux RSS ne les contiennent pas, et on ne visite jamais les pages des articles. |

Test : `tests/test_retention.py::test_payload_sans_donnees_personnelles_ni_texte_integral`.

**À noter :** les champs auteurs, crédits photo, images et résumés complets ont été exclus le 12/11/2026. Les bruts collectés avant cette date les contiennent encore. Ils sortiront de `data/articles_bruts/` par la rétention, mais resteront dans les archives d'un an, sauf si on les épure.

## 12. Traçabilité

D'un article propre (ou d'un rejet), on remonte jusqu'au brut et à la source :

```
articles_propres.id ─────────┐  même id que le brut : sha256(source, lien)
articles_propres.chemin_brut ┴─> data/articles_bruts/2026-11-12/le-monde/<id>.json   (30 j)
                                 data/archives/articles_bruts/2026-11-12.tar.gz       (jusqu'à 1 an)
articles_propres.source ───────> sources.nom ─> sources.url_flux
```

- `qualite.retention.retrouver_brut(chemin_brut, cfg)` lit le brut là où il se trouve : dans le fichier actif, ou dans l'archive du jour.
- Le brut garde le `payload` d'origine (moins les champs exclus), ce qui permet de rejouer le traitement.
- Un rejet garde aussi la règle et le détail qui l'ont écarté.

Tests : `tests/test_traitement.py::test_traitement_repartit_et_trace` (article propre vers fichier brut) et `tests/test_retention.py::test_tracabilite_apres_archivage` (article propre vers brut archivé).
