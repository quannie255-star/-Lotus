"""quality/run.py — quality 阶段入口（M6）

链路：dedup 产物（latest_outputs 验货）→ 五维打分（污染索引若有 benchmark）→ 门禁三分
→ data/quality/source={s}/part-*.parquet（放行行 + 分数列；stage_extensions 落表）
→ changelog（low_quality 删除 / quarantine 隔离）+ gate_report.json。
"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from common import log
from common.idempotent import run_stage
from common.stage import StagedChangeLog, latest_outputs, snapshot_configs
from ingest.spine_schema import SPINE_PARQUET_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "quality-0.1.0"
DATA_ROOT = ROOT / "data"
STAGE = "quality"
STAGING_CHANGELOG = DATA_ROOT / "changelog.staging.jsonl"

CONFIGS = [ROOT / "configs" / "quality_policy.json", ROOT / "configs" / "reason_registry.json"]

QUALITY_EXT_SCHEMA = pa.schema(list(SPINE_PARQUET_SCHEMA) + [
    pa.field("quality_completeness", pa.float64()),
    pa.field("quality_uniqueness", pa.float64()),
    pa.field("quality_consistency", pa.float64()),
    pa.field("quality_non_pollution", pa.float64()),
    pa.field("quality_length", pa.float64()),
    pa.field("quality_score", pa.float64(), nullable=False),
    pa.field("quality_model_version", pa.string(), nullable=False),
    pa.field("quality_run_id", pa.string(), nullable=False),
])


def run(force: bool = False, output_root: Path | None = None, runs_path=None,
        config_paths=None, dedup_root: Path | None = None, with_contamination: bool = True) -> dict:
    """quality 阶段入口。

    返回: {cached, run_id, rows, removed, quarantined, changelog_added, report}
    异常: UpstreamNotReadyError — dedup 未跑/被篡改
    幂等: 指纹=dedup 产物+配置快照（含 quality_policy）；重算字节一致
    """
    out_root = Path(output_root) if output_root else DATA_ROOT
    configs = [Path(p) for p in (config_paths or CONFIGS)]
    snaps = snapshot_configs(configs, out_root / f".{STAGE}.configs")
    inputs = latest_outputs(Path(dedup_root) if dedup_root else DATA_ROOT, only="dedup")

    from quality.engine import build_ngram_index, gate, policy, score_row

    benchmarks = [ROOT / b for b in policy()["dims"]["non_pollution"]["benchmark_files"]]
    index = build_ngram_index(benchmarks) if with_contamination else None
    if index is not None and not index:
        index = None  # benchmark 未就绪 → 显式 skipped 而非空集假检测

    holder = {"slog": None}
    summary = {}

    def transform(_input_paths, work_dir, run_id):
        slog = StagedChangeLog(STAGING_CHANGELOG, run_id=run_id)
        holder["slog"] = slog
        rows = []
        for path in inputs:
            if Path(path).name == "signatures.parquet" or Path(path).name == "report.json":
                continue  # latest_outputs 只回 part-（stage.py 过滤）——防御性再查一次
            for batch in pq.ParquetFile(path).iter_batches(batch_size=2000):
                rows.extend(batch.to_pylist())

        outputs = []
        by_source, report = {}, {"pass": 0, "hold": 0, "remove": 0}
        for row in rows:
            scores = score_row(row, contamination_index=index)
            verdict, reason = gate(scores)
            report[verdict] += 1
            scored = {**row, **scores, "quality_model_version": PIPELINE_VERSION,
                      "quality_run_id": run_id}
            if verdict == "remove":
                slog.log({"doc_id": row["doc_id"], "source": row["source"], "stage": "quality",
                          "action": "remove", "reason": reason, "changed_by": "quality:gate",
                          "old_hash": row.get("content_hash"), "review_status": "auto",
                          "detail": json.dumps({k: scores[k] for k in scores
                                                if k.startswith("quality_")}, ensure_ascii=False)})
                continue
            if verdict == "hold":
                slog.log({"doc_id": row["doc_id"], "source": row["source"], "stage": "quality",
                          "action": "quarantine", "reason": reason, "changed_by": "quality:gate",
                          "old_hash": row.get("content_hash"), "review_status": "pending_review",
                          "detail": json.dumps({"quality_score": scores["quality_score"]})})
            scored.pop("pollution_skipped", None)
            by_source.setdefault(row["source"], []).append(scored)

        for source, kept in sorted(by_source.items()):
            out_path = Path(work_dir) / f"source={source}" / "part-00000.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(kept, schema=QUALITY_EXT_SCHEMA), out_path)
            outputs.append(out_path)

        gate_report = {**report, "pollution_index_ready": index is not None,
                       "total": len(rows), "thresholds": policy()["gate"]}
        (Path(work_dir) / "gate_report.json").write_text(
            json.dumps(gate_report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        outputs.append(Path(work_dir) / "gate_report.json")
        summary.update(gate_report)
        return outputs

    try:
        result = run_stage(STAGE, transform, inputs + snaps, out_root,
                           force=force, runs_path=runs_path, pipeline_version=PIPELINE_VERSION)
    except Exception:
        if holder["slog"] is not None:
            holder["slog"].discard()
        raise

    slog = holder["slog"]
    if result["cached"] or slog is None:
        if slog is not None:
            slog.discard()
    else:
        result["changelog_added"] = slog.commit(result["run_id"])
    result["rows"] = summary.get("pass")
    result["report"] = summary
    return result


def main(argv=None):  # pragma: no cover
    import sys

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
