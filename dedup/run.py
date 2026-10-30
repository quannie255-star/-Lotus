"""dedup/run.py — dedup 阶段入口（M5，家族契约）

链路：clean 产物（latest_outputs 验货）→ normalized_content_hash + MinHash 引擎
→ data/dedup/source={s}/part-*.parquet（仅代表行）+ signatures.parquet + report.json
→ changelog（exact_dup/near_dup，带 ref_doc_id/similarity/cluster_id）。
"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from common import ids, log
from common.idempotent import run_stage
from common.stage import StagedChangeLog, latest_outputs, snapshot_configs
from ingest.spine_schema import SPINE_PARQUET_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "dedup-0.1.0"
DATA_ROOT = ROOT / "data"
CLEAN_ROOT = DATA_ROOT / "clean"
STAGE = "dedup"
STAGING_CHANGELOG = DATA_ROOT / "changelog.staging.jsonl"

CONFIGS = [ROOT / "configs" / "dedup_policy.json", ROOT / "configs" / "reason_registry.json",
           ROOT / "configs" / "text_normalization.json"]  # 规范化口径变了 = 哈希变 = 必须重算

SIGNATURE_SCHEMA = pa.schema([
    pa.field("doc_id", pa.string(), nullable=False),
    pa.field("signature", pa.string(), nullable=False),
    pa.field("shingle_unit", pa.string(), nullable=False),
    pa.field("shingle_n", pa.int64(), nullable=False),
    pa.field("num_perm", pa.int64(), nullable=False),
    pa.field("algorithm", pa.string(), nullable=False),
    pa.field("run_id", pa.string(), nullable=False),
])


def run(force: bool = False, output_root: Path | None = None, runs_path=None,
        config_paths=None, clean_root: Path | None = None) -> dict:
    """dedup 阶段入口。

    返回: {cached, run_id, rows, removed, changelog_added, report, sources}
    异常: UpstreamNotReadyError — clean 未跑/被篡改；ValueError — clean 为空
    幂等: 指纹=clean part 文件+三份配置快照；重算字节一致
    """
    out_root = Path(output_root) if output_root else DATA_ROOT
    configs = [Path(p) for p in (config_paths or CONFIGS)]
    snaps = snapshot_configs(configs, out_root / f".{STAGE}.configs")
    inputs = latest_outputs(Path(clean_root) if clean_root else DATA_ROOT)
    if not inputs:
        raise ValueError("clean 层没有产物——先跑 clean")

    from dedup.engine import dedup_pass, policy

    holder = {"slog": None}
    summary = {}

    def transform(_input_paths, work_dir, run_id):
        slog = StagedChangeLog(STAGING_CHANGELOG, run_id=run_id)
        holder["slog"] = slog
        rows = []
        for path in inputs:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=2000):
                rows.extend(batch.to_pylist())
        for row in rows:  # 规范化哈希在此算（消费 text_normalization 配置，进指纹）
            row["normalized_hash"] = ids.normalized_content_hash(row["content"])

        result = dedup_pass(rows)
        pol = policy()

        outputs = []
        by_source = {}
        for row in result["keep"]:
            by_source.setdefault(row["source"], []).append(row)
        for source, kept in sorted(by_source.items()):
            out_path = Path(work_dir) / f"source={source}" / "part-00000.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(
                [{k: v for k, v in r.items() if k != "normalized_hash"} for r in kept],
                schema=SPINE_PARQUET_SCHEMA), out_path)
            outputs.append(out_path)

        sig_rows = [{"doc_id": d, "signature": s, "shingle_unit": pol["shingle"]["unit"],
                     "shingle_n": pol["shingle"]["n"], "num_perm": pol["minhash"]["num_perm"],
                     "algorithm": "minhash", "run_id": run_id}
                    for d, s in sorted(result["signatures"].items())]
        sig_path = Path(work_dir) / "signatures.parquet"
        pq.write_table(pa.Table.from_pylist(sig_rows, schema=SIGNATURE_SCHEMA), sig_path)
        outputs.append(sig_path)

        for entry in sorted(result["removed"], key=lambda e: e["row"]["doc_id"]):
            row = entry["row"]
            slog.log({
                "doc_id": row["doc_id"], "source": row["source"], "stage": "dedup",
                "action": "remove", "reason": entry["reason"],
                "changed_by": f"dedup:{entry['reason']}",
                "old_hash": row.get("content_hash"),
                "ref_doc_id": entry["ref_doc_id"],
                "similarity": entry["similarity"],
                "dup_cluster_id": entry["cluster_id"],
            })

        report = {
            "input_rows": len(rows),
            "kept": len(result["keep"]),
            "removed_exact": sum(1 for e in result["removed"] if e["reason"] == "exact_dup"),
            "removed_near": sum(1 for e in result["removed"] if e["reason"] == "near_dup"),
            "exact_groups": result["exact_groups"],
            "near_clusters": sum(1 for c in result["clusters"] if c.startswith("near:")),
            "cross_source_overlap": {f"{a}|{b}": n for (a, b), n in
                                     sorted(result["cross_source"].items())},
        }
        report_path = Path(work_dir) / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                               encoding="utf-8")
        outputs.append(report_path)
        summary.update(report)
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
    result["rows"] = summary.get("kept")
    result["report"] = summary
    return result


def main(argv=None):  # pragma: no cover
    import sys

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
