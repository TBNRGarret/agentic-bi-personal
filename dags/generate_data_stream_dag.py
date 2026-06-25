"""
Airflow DAG: Generate Data Stream
Generates synthetic streaming data for the previous completed hour and
uploads JSONL batches + manifests to MinIO.

Replaces the previous manual screen-based execution of
scripts/generate_data_stream.py.

Schedule: @hourly — runs at :00, generates data for the previous hour.
Retries: up to 3 times with 5-minute delay between attempts.
"""

from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta

with DAG(
    dag_id="generate_data_stream",
    schedule="@hourly",
    start_date=datetime(2026, 6, 25),
    catchup=False,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
    },
    description="Generate synthetic streaming data for the previous hour and upload to MinIO",
    tags=["data_generation", "stream"],
) as dag:

    generate_and_upload = BashOperator(
        task_id="generate_and_upload",
        bash_command=(
            "python /opt/airflow/scripts/generate_data_stream.py "
            "--save --hour '{{ data_interval_start.strftime('%Y-%m-%d-%H') }}'"
        ),
    )
