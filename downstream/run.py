"""downstream/run.py — 四臂消融 ppl 闭环（M8，唯一核心指标的执行器）

四臂（blueprint §4 消融归因）：raw / clean / dedup / release —— 各臂抽同规模语料
训一个 CharNgramLM，在同一冻结评测集上比 ppl。评测集来自 clean 放行样本（独立冻结，
不参与任何臂训练），bootstrap CI。报告写 data/downstream/report.json + md。
"""

import json
import random
from pathlib import Path

from common import log
from common.idempotent import run_stage
from common.stage import snapshot_configs

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "downstream-0.1.0"
DATA_ROOT = ROOT / "data"
RAW_ROOT = DATA_ROOT / "raw"
STAGE = "downstream"
CONFIGS = [ROOT / "configs" / "quality_policy.json"]

TRAIN_CHARS = 1_500_000      # 每臂训练语料上限（控制时长；口径进报告）
EVAL_N = 600                 # 评测集行数
EVAL_SEED = 2026             # 评测集种子——一经发布永不改（与 doc_id 同纪律）


def _lines_from(parts, n=None, seed=None):
    import pyarrow.parquet as pq

    lines = []
    for path in parts:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=500, columns=["content"]):
            lines.extend(v for v in batch.column("content").to_pylist() if v)
    if n is not None:
        rng = random.Random(seed)
        rng.shuffle(lines)
        lines = lines[:n]
    return lines


def run(force: bool = False, output_root: Path | None = None, runs_path=None,
        config_paths=None, eval_lines: list | None = None) -> dict:
    """四臂 ppl 对比。评测集默认从 clean 层冻结（eval_lines 可注入——测试/复用固定集）。

    返回: {cached, run_id, arms: {arm: {ppl, ci, trained_chars}}, eval_n}
    异常: UpstreamNotReadyError — clean/dedup/release 任一未就绪（四臂须同批对比）
    """
    out_root = Path(output_root) if output_root else DATA_ROOT
    configs = [Path(p) for p in (config_paths or CONFIGS)]
    snaps = snapshot_configs(configs, out_root / f".{STAGE}.configs")

    all_parts = _all_parts()
    norm = lambda p: str(p).replace("\\", "/")  # noqa: E731
    arms_src = {
        "raw": [p for p in all_parts if "/data/raw/" in norm(p)],
        "clean": [p for p in all_parts if "/data/clean/" in norm(p)],
        "dedup": [p for p in all_parts if "/data/dedup/" in norm(p)],
        "release": [p for p in all_parts if "/data/release/" in norm(p)],
    }
    summary = {}

    def transform(_inputs, work_dir, run_id):
        from downstream.ppl import CharNgramLM, bootstrap_ci, corpus_text

        if eval_lines is None:
            eval_lines_ = _lines_from(arms_src["clean"], n=EVAL_N, seed=EVAL_SEED)
        else:
            eval_lines_ = eval_lines
        report = {"eval_n": len(eval_lines_), "engine": f"char-{CharNgramLM().order}gram-alpha{CharNgramLM().alpha}",
                  "train_chars_cap": TRAIN_CHARS, "arms": {}}
        for arm, parts in sorted(arms_src.items()):
            if not parts:
                report["arms"][arm] = {"skipped": "上游未就绪"}
                continue
            lm = CharNgramLM().train(corpus_text(parts, TRAIN_CHARS))
            ppl = lm.perplexity(eval_lines_)
            ci = bootstrap_ci(lm, eval_lines_)
            report["arms"][arm] = {"ppl": round(ppl, 2), "ci95": ci,
                                   "trained_chars": lm.trained_chars}
        (Path(work_dir) / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        summary.update(report)
        return [Path(work_dir) / "report.json"]

    result = run_stage(STAGE, transform, [p for ps in arms_src.values() for p in ps] + snaps,
                       out_root, force=force, runs_path=runs_path,
                       pipeline_version=PIPELINE_VERSION)
    result["arms"] = summary.get("arms", {})
    result["eval_n"] = summary.get("eval_n")
    return result


def _all_parts() -> list:
    """raw/clean/dedup/release 四层全部 part 文件（raw 无统一 manifest——直接 glob）。"""
    parts = []
    for zone in ("raw", "clean", "dedup", "release"):
        zone_dir = DATA_ROOT / zone
        if not zone_dir.exists():
            continue
        parts.extend(sorted(zone_dir.glob("**/part-*.parquet"), key=str))
    return parts


def main(argv=None):  # pragma: no cover
    import sys

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
