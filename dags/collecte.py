"""DAG de collecte : toutes les 30 minutes, sans que personne n'appuie.

migrer -> sync_sources -> collecter -> verifier_sommeil

- `collecter` gere lui-meme les reprises par source (backoff) : pas de retry Airflow,
  qui relancerait toutes les sources d'un coup.
- `verifier_sommeil` passe en rouge si une source a ete mise en sommeil :
  c'est l'alerte visible dans l'interface Airflow (« quelqu'un doit le voir »).
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

CODE = "/opt/gazette"

with DAG(
    dag_id="collecte",
    description="Collecte des flux RSS/Atom vers articles_bruts",
    schedule="*/30 * * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,                      # pas de rattrapage : un flux ne se relit pas au passe
    max_active_runs=1,                  # jamais deux collectes en parallele
    is_paused_upon_creation=False,      # demarre seul des que le DAG est charge
    dagrun_timeout=timedelta(minutes=25),
    default_args={"retries": 0, "cwd": CODE},
    tags=["gazette", "collecte"],
) as dag:
    migrer = BashOperator(task_id="migrer", bash_command="python -m collect migrer")
    sync = BashOperator(task_id="sync_sources", bash_command="python -m collect sync-sources")
    collecter = BashOperator(
        task_id="collecter",
        bash_command="python -m collect collecter",
        execution_timeout=timedelta(minutes=20),
    )
    verifier = BashOperator(task_id="verifier_sommeil", bash_command="python -m collect verifier")

    migrer >> sync >> collecter >> verifier
