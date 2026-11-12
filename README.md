# La Gazette

Revue de presse automatisée. Cadrage : [docs/Gazette – cadrage.md](docs/Gazette%20–%20cadrage.md).

## Étape 1 : la collecte

Toutes les 30 minutes, le collecteur lit les flux RSS/Atom des sources actives ([sources/sources.csv](sources/sources.csv)). Il écrit chaque nouvel article dans un fichier JSON sous `data/articles_bruts/` et tient à jour la table `sources` (dernière réussite, échecs, ETag). Les champs sont décrits dans [docs/donnees.md](docs/donnees.md).

```
collect/
  config.py      configuration (variables d'environnement / .env)
  reseau.py      HTTP : User-Agent, If-None-Match / If-Modified-Since, timeouts, backoff
  items.py       RSS 2.0 et Atom -> format articles_bruts (via feedparser)
  stockage.py    un fichier JSON par article, dédoublonnage sur (source, lien)
  db.py          migrations versionnées + état des sources
  collecteur.py  un passage : toutes les sources, une ligne de journal par source
  __main__.py    ligne de commande
qualite/                   traitement (TD 4) : normalisation, regles R01..R11 -> articles_propres | rejets
dags/collecte.py           orchestration Airflow (*/30 min) : collecter puis traiter
dags/retention.py          rétention quotidienne (3 h UTC) : bruts archivés à 30 j, rejets 30 j, propres 1 an
sql/migrations/V001__...   migration de la table sources
sql/migrations/V002__...   tables articles_propres et rejets
sql/migrations/V003__...   table metriques_qualite (contrôles)
tests/                     « source en panne » et « deux passages, zéro doublon »
```

### Lancer en local, sans Docker

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/Mac : source .venv/bin/activate
pip install -r requirements.txt
python -m collect tout        # migrer + sync-sources + collecter (SQLite dans data/gazette.db)
python -m collect etat        # état des sources
python -m qualite traiter     # brut -> articles_propres | rejets (idempotent)
python -m qualite controler   # contrôles de qualité : métriques, code retour 1 si un fait est faux
python -m qualite rapport     # rapport du jour -> rapports/qualite-AAAA-MM-JJ.md
python -m qualite retention --simulation   # ce que la rétention archiverait / supprimerait
python -m pytest              # tests
```

Pour viser le MySQL du docker-compose depuis la machine : `DATABASE_URL=mysql+pymysql://gazette:gazette@localhost:3307/gazette_db` (port modifiable avec `MYSQL_PORT`).

### Lancer avec Docker : la collecte tourne seule

```bash
docker compose up -d --build
```

Airflow est sur http://localhost:8080. L'utilisateur est `admin` ; le mot de passe s'affiche avec `docker compose logs airflow | grep password`. Le DAG `collecte` démarre seul, sans qu'il faille le dé-pauser, et tourne toutes les 30 minutes tant que le conteneur est allumé :

```
migrer -> sync_sources -> collecter -+-> verifier_sommeil
                                     +-> traiter -> controler -> rapport
```

`traiter` part de `collecter`, pas de `verifier_sommeil` : une source en sommeil n'empêche pas de traiter ce que les autres ont ramené.

Commandes utiles :

```bash
docker compose exec airflow bash -c "cd /opt/gazette && python -m collect etat"
docker compose exec airflow bash -c "cd /opt/gazette && python -m collect reveiller 'Les Échos'"
```

### Pourquoi Airflow

| Besoin | Ce qu'apporte Airflow |
|---|---|
| Tourner toutes les 30 min sans personne | Planificateur intégré (`schedule="*/30 * * * *"`), DAG actif dès sa création |
| Orchestration décrite en code, dans le dépôt | Le DAG est un fichier Python versionné ([dags/collecte.py](dags/collecte.py)) |
| Voir qu'une source est en sommeil | La tâche `verifier_sommeil` passe en rouge ; historique et logs par tâche dans l'interface |
| Ne pas lancer deux collectes en même temps | `max_active_runs=1`, `catchup=False` |
| Suite du projet (traitement, clustering, publication à 7 h) | Les étapes s'ajoutent comme tâches dépendantes dans le même outil |

Options écartées :

- **cron** : simple, mais pas d'historique, pas d'interface, et une alerte à construire soi-même.
- **GitHub Actions** : déjà écarté dans le cadrage. Il est pensé pour la CI, ses horaires planifiés sont approximatifs, et les données (fichiers, MySQL) ne vivent pas sur ses runners.
- **Prefect / Dagster** : comparables, mais l'équipe part déjà sur Airflow pour tout le pipeline.

Contrepartie assumée : Airflow est lourd (3 conteneurs avec sa base). Pour l'instant il tourne sur la machine d'un·e membre de l'équipe ; la production viendra plus tard.

### Politesse envers les éditeurs

- Un User-Agent qui dit qui nous sommes (`LaGazetteBot/0.1 (...; +url du dépôt)`).
- `ETag` et `Last-Modified` renvoyés : un flux inchangé répond 304 et rien n'est retéléchargé.
- Une fréquence de 30 min, un seul passage à la fois, des sources traitées l'une après l'autre.
- Après 3 échecs de suite, on arrête d'insister : la source passe en sommeil.
