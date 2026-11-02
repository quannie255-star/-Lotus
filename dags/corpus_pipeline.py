"""dags/corpus_pipeline.py — Airflow DAG 定义（M9，编排层）

拓扑：四源 ingest 并行扇入 → clean → dedup → quality → mix → release。
每个任务 = BashOperator 调 `python -m <module>`，模块自带幂等（run_stage 的
cached/force 语义与 Airflow retries=2 天然咬合——重试即幂等重入）。

⚠️ 激活前提（记录在 DEVLOG M9 遗留）：airflow 容器需挂载本仓库并安装 requirements
（当前 compose 未做仓库挂载）。DAG 文件先行入库，编排验证在环境补齐后做。
"""

from datetime import timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.dates import days_ago

DEFAULT_ARGS = {
    "owner": "quannie",
    "retries": 2,
    "retry_delay": timedelta(minutes=1),
    "retry_exponential_backoff": True,
}

STAGES = {
    "ingest_wiki": "python -m ingest.wiki",
    "ingest_oscar": "python -m ingest.oscar",
    "ingest_news": "python -m ingest.news",
    "ingest_pdf": "python -m ingest.pdf",
    "clean": "python -m clean.run",
    "dedup": "python -m dedup.run",
    "quality": "python -m quality.run",
    "mix": "python -m mix.run",
    "release": "python -m release.run",
    "downstream": "python -m downstream.run",
}

with DAG(
    dag_id="corpus_pipeline",
    default_args=DEFAULT_ARGS,
    description="Lotus 濯莲语料生产管道：采集→清洗→去重→质量→配比→发布→下游验证",
    schedule_interval=None,
    start_date=days_ago(1),
    catchup=False,
    tags=["lotus"],
) as dag:
    tasks = {
        name: BashOperator(task_id=name, bash_command=cmd, cwd="/opt/airflow/dags")
        for name, cmd in STAGES.items()
    }
    # 扇入扇出拓扑：四源并行 → 线性主链
    for src in ("ingest_wiki", "ingest_oscar", "ingest_news", "ingest_pdf"):
        tasks[src] >> tasks["clean"]
    tasks["clean"] >> tasks["dedup"] >> tasks["quality"] >> tasks["mix"] >> tasks["release"] >> tasks["downstream"]
