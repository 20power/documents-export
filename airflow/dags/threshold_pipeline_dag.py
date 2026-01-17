# -*- coding: utf-8 -*-
from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime

default_args = {"owner": "claims", "depends_on_past": False, "retries": 0}

with DAG(
    dag_id="injury_claims_threshold_pipeline",
    default_args=default_args,
    start_date=datetime(2025, 1, 1),
    schedule_interval=None,
    catchup=False,
) as dag:
    t_train = BashOperator(
        task_id="train_qr_multihead",
        bash_command="python ./train_quantile_multihead.py --config ./config_qr.yaml"
    )

    t_infer = BashOperator(
        task_id="offline_infer_snapshot",
        bash_command="python ./infer_quantile_multihead.py"
    )

    # 打包模型与报告，并按日期归档
    t_package = BashOperator(
        task_id="package_and_archive",
        bash_command=(
            "set -e;"
            "OUT=./qr_multihead_outputs; "
            "DATE=$(date +%Y%m%d_%H%M%S); "
            "ZIP=./artifacts/qr_models_${DATE}.zip; "
            "mkdir -p ./artifacts; "
            "zip -r "$ZIP" "$OUT/models" "$OUT/meta.json" "$OUT/qr_report.csv" || true; "
            "echo "Archived -> $ZIP""
        )
    )

    t_train >> t_infer >> t_package
