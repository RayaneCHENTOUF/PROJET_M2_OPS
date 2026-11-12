"""DAG de retention : une fois par jour, a 3 h UTC (hors des heures de lecture).

- bruts de plus de 30 jours : archives (un .tar.gz par jour dans data/archives/articles_bruts/),
  archives supprimees apres 1 an ;
- rejets : supprimes apres 30 jours ;
- articles_propres : supprimes apres 1 an.

Politique complete et justification : docs/donnees.md, § Retention.
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="retention",
    description="Applique la duree de retention ecrite dans docs/donnees.md",
    schedule="0 3 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args={"retries": 0, "cwd": "/opt/gazette"},
    tags=["gazette", "qualite"],
) as dag:
    BashOperator(
        task_id="appliquer_retention",
        bash_command="python -m qualite retention",
        execution_timeout=timedelta(minutes=30),
    )
