"""DAG de collecte : toutes les 30 minutes, sans que personne n'appuie.

migrer -> sync_sources -> collecter -+-> verifier_sommeil
                                     +-> traiter -> controler -> rapport

- `collecter` gere lui-meme les reprises par source (backoff) : pas de retry Airflow,
  qui relancerait toutes les sources d'un coup.
- `verifier_sommeil` passe en rouge si une source a ete mise en sommeil :
  c'est l'alerte visible dans l'interface Airflow (« quelqu'un doit le voir »).
- `traiter` (TD 4) : brut -> normalisation -> regles -> articles_propres | rejets.
  Il part de `collecter` et non de `verifier_sommeil` : une source en sommeil
  ne doit pas empecher de traiter ce que les autres ont ramene.
  Idempotent (un article deja traite est saute) : on peut relancer la tache sans risque.
- `controler` verifie des FAITS sur les vraies donnees (aucune date future, aucun titre
  vide, taux de rejet par source <= 80 %), publie les metriques (table metriques_qualite)
  et passe en rouge si un fait est faux : c'est l'alerte. Pas la CI, qui teste le code.
- `rapport` reecrit rapports/qualite-AAAA-MM-JJ.md (un fichier par jour). Il tourne
  meme si `controler` est rouge (trigger_rule all_done) : c'est la qu'on lit pourquoi.
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.trigger_rule import TriggerRule

CODE = "/opt/gazette"

with DAG(
    dag_id="collecte",
    description="Collecte des flux RSS/Atom, puis traitement qualite vers articles_propres",
    schedule="*/30 * * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,                      # pas de rattrapage : un flux ne se relit pas au passe
    max_active_runs=1,                  # jamais deux collectes en parallele
    is_paused_upon_creation=False,      # demarre seul des que le DAG est charge
    dagrun_timeout=timedelta(minutes=25),
    default_args={"retries": 0, "cwd": CODE},
    tags=["gazette", "collecte", "qualite"],
) as dag:
    migrer = BashOperator(task_id="migrer", bash_command="python -m collect migrer")
    sync = BashOperator(task_id="sync_sources", bash_command="python -m collect sync-sources")
    collecter = BashOperator(
        task_id="collecter",
        bash_command="python -m collect collecter",
        execution_timeout=timedelta(minutes=20),
    )
    verifier = BashOperator(task_id="verifier_sommeil", bash_command="python -m collect verifier")

    traiter = BashOperator(
        task_id="traiter",
        bash_command="python -m qualite traiter",
        execution_timeout=timedelta(minutes=5),
    )

    controler = BashOperator(
        task_id="controler",
        bash_command="python -m qualite controler",
        execution_timeout=timedelta(minutes=2),
    )

    rapport = BashOperator(
        task_id="rapport",
        bash_command="python -m qualite rapport",
        execution_timeout=timedelta(minutes=2),
        trigger_rule=TriggerRule.ALL_DONE,  # le rapport s'ecrit aussi quand un controle sonne
    )

    migrer >> sync >> collecter >> [verifier, traiter]
    traiter >> controler >> rapport
