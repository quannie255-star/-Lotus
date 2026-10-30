"""clean/run.py — clean 阶段统一入口（M4，家族契约 + WP-01 六件套首次合体）

链路：各源 raw 产物（latest_outputs 验货）→ 规则链（iter_clean，StagedChangeLog 暂存）
→ 按源写 clean/docs/source={s}/part-00000.parquet → run_stage 幂等 → 成功后 commit changelog
（run_id 由 commit 注入，行内绝不携带"pending"这类占位身份）。配置经 snapshot_configs
进指纹——改配置必重算（修 M1 遗留「指纹不含配置」）。
"""

import json
from functools import lru_cache
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from common import log
from common.idempotent import run_stage
from common.stage import StagedChangeLog, latest_outputs, snapshot_configs
from ingest.spine_schema import SPINE_PARQUET_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "clean-0.1.0"
RAW_ROOT = ROOT / "data" / "raw"
DATA_ROOT = ROOT / "data"
STAGE = "clean"
STAGING_CHANGELOG = DATA_ROOT / "changelog.staging.jsonl"

CONFIGS = [ROOT / "configs" / "clean_policy.json", ROOT / "configs" / "reason_registry.json"]


@lru_cache(maxsize=1)
def _sources_with_raw():
    """有 raw 产物的源（目录下有 manifest）。缺的源跳过——源可增量接入，不阻断全家。"""
    if not RAW_ROOT.exists():
        return ()
    return tuple(child.name for child in sorted(RAW_ROOT.iterdir())
                 if child.is_dir() and (child / "manifest.json").exists())


def _iter_rows(paths):
    for path in paths:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=2000):
            yield from batch.to_pylist()


def run(force: bool = False, output_root: Path | None = None, runs_path=None,
        sources: tuple | None = None, config_paths=None) -> dict:
    """clean 阶段入口（家族 run 契约）。

    参数: sources — 限定源（None=全部有 raw 的）；config_paths — 测试注入配置
    返回: {cached, run_id, output_dir, output_hashes, rows, changelog_added, sources, stats}
    异常: UpstreamNotReadyError — 某源 raw manifest 缺失/产物被篡改（fail-fast）
    幂等: 指纹 = raw part 文件 + 配置快照；重算字节一致；changelog 两段式（失败零痕迹）
    """
    out_root = Path(output_root) if output_root else DATA_ROOT
    configs = [Path(p) for p in (config_paths or CONFIGS)]
    snaps = snapshot_configs(configs, out_root / f".{STAGE}.configs")
    chosen = tuple(sources) if sources else _sources_with_raw()
    if not chosen:
        raise ValueError("没有任何源有 raw 产物——先跑各源 ingest")

    inputs, grouped = [], {s: [] for s in chosen}
    for source in chosen:
        parts = latest_outputs(RAW_ROOT / source)
        grouped[source] = parts
        inputs.extend(parts)

    from clean.base import iter_clean, load_rules

    rules = load_rules()
    holder = {"slog": None}
    counters = {"rows": 0, "by_source": {}, "stats": {}}

    def transform(_input_paths, work_dir, run_id):
        # slog 在 transform 内创建：此刻才有真实 run_id（schema 必填项，不许占位身份进账本）
        slog = StagedChangeLog(STAGING_CHANGELOG, run_id=run_id)
        holder["slog"] = slog
        outputs = []
        for source, parts in grouped.items():
            rows_iter, stats = iter_clean(_iter_rows(parts), rules, slog, changed_by="clean")
            kept = list(rows_iter)
            out_path = Path(work_dir) / f"source={source}" / "part-00000.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(kept, schema=SPINE_PARQUET_SCHEMA), out_path)
            counters["rows"] += len(kept)
            counters["by_source"][source] = {"kept": len(kept)}
            counters["stats"][source] = stats.get(source, {})
            outputs.append(out_path)
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
    result["rows"] = counters["rows"] or None
    result["sources"] = list(chosen)
    result["stats"] = counters["stats"]
    return result


def main(argv=None):  # pragma: no cover
    import sys

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps({k: v for k, v in result.items() if k != "output_hashes"},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
