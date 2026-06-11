"""
Airflow DAG: dbt Pipeline
Runs dbt build (staging + marts) followed by dbt test (data quality checks).
"""

from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta

DBT_PROJECT_DIR = "/opt/airflow/dbt"
DBT_BINARY = "/home/airflow/.local/bin/dbt"

with DAG(
    dag_id="dbt_pipeline",
    schedule="@hourly",
    start_date=datetime(2026, 6, 10),
    catchup=False,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    description="dbt build + test pipeline",
) as dag:
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=f"cd {DBT_PROJECT_DIR} && dbt build --profiles-dir .",
    )
    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"cd {DBT_PROJECT_DIR} && dbt test --profiles-dir .",
    )
    dbt_build >> dbt_test
