# Dictionnaire des données — étape de collecte

La collecte produit deux choses : des **fichiers bruts** (un par article) et une **table `sources`** qui dit où en est chaque flux.

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
| `payload`     | objet                 | L'item tel que lu par feedparser (dates converties en ISO), **sans le champ `content`**, qui peut contenir le texte intégral. Permet de rejouer le traitement sans re-collecter. |

**Clé naturelle : `(source, lien)`.** Le `guid` aide, mais il n'est pas fiable partout (absent, pas une URL, ou régénéré). Un article déjà écrit pour un `(source, lien)` donné, quel que soit le jour, n'est jamais réécrit : lancer la collecte deux fois ne crée aucun doublon.

**Ce qu'on ne garde pas :** le texte des articles, les commentaires, toute donnée personnelle.

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
