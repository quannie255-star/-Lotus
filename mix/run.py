"""mix/run.py — 配比与确定性分层抽样（M7）

链路：quality 放行行 → 体裁/源分层 → 各层按 ratios 分 token 预算 → 层内质量分降序
候选 + 种子洗牌确定性选取至预算 → data/mix/candidates/part-*.parquet（附 token_count/
sampling_weight/mix_batch_id/tokenizer 标识）。层枯竭按剩余层等比再分配并记录。
"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from common import log
from common.idempotent import run_stage
from common.stage import latest_outputs, snapshot_configs
from ingest.spine_schema import SPINE_PARQUET_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "mix-0.1.0"
DATA_ROOT = ROOT / "data"
STAGE = "mix"
CONFIGS = [ROOT / "configs" / "mix_policy.json"]

MIX_EXT_SCHEMA = pa.schema(list(SPINE_PARQUET_SCHEMA) + [
    pa.field("token_count", pa.int64(), nullable=False),
    pa.field("tokenizer_name", pa.string(), nullable=False),
    pa.field("tokenizer_version", pa.string(), nullable=False),
    pa.field("sampling_weight", pa.float64(), nullable=False),
    pa.field("mix_batch_id", pa.string(), nullable=False),
])


def token_count_estimate(text: str) -> int:
    """确定性字符估计（tokenizer name=char-estimate-v1）。

    口径：CJK 码点 1 token/字；其余 4 字符≈1 token。纯函数、零依赖——transformers
    Qwen 分词器是升级位（改 mix_policy.tokenizer.name 并 force）。
    """
    from common.textstats import cjk_char_count
    cjk = cjk_char_count(text)
    return cjk + max((len(text) - cjk) // 4, 0)


def stratified_sample(rows: list, pol: dict, run_id: str) -> tuple:
    """确定性分层抽样：层=source；层内按质量降序（doc_id tiebreak）贪心取满 token 目标；
    层枯竭的剩余预算单轮等比再分配给仍有余量的层（单轮近似，报告记录枯竭）。

    参数: rows — quality 放行行；pol — mix_policy
    返回: (chosen, report)；report[layer] = {target_tokens, tokens, docs, exhausted}
    幂等: 全程无随机数（洗牌经质量排序替代）；同输入同输出——seed 留给未来加权抽样升级
    """
    budget = pol["token_budget"]
    ratios = pol["ratios"]
    strata = {}
    for row in rows:
        strata.setdefault(row["source"], []).append(row)
    for source in strata:
        strata[source].sort(key=lambda r: (-r.get("quality_score", 1.0), r["doc_id"]))

    present_ratio_sum = sum(ratios.get(s, 0) for s in strata) or 1.0
    targets = {s: budget * ratios.get(s, 0) / present_ratio_sum for s in strata}

    def take(layer: str, target: float) -> tuple:
        got, picked = 0, []
        for row in strata[layer]:
            if got >= target:
                break
            tc = token_count_estimate(row["content"])
            picked.append({**row, "token_count": tc,
                           "tokenizer_name": pol["tokenizer"]["name"],
                           "tokenizer_version": pol["tokenizer"]["name"],
                           "sampling_weight": 1.0, "mix_batch_id": run_id})
            got += tc
        strata[layer] = strata[layer][len(picked):]   # 剩余量供再分配轮使用
        return picked, got

    chosen, report = [], {}
    for layer in sorted(targets):
        picked, got = take(layer, targets[layer])
        chosen.extend(picked)
        report[layer] = {"target_tokens": int(targets[layer]), "tokens": got,
                         "docs": len(picked),
                         "exhausted": bool(strata[layer]) and got < targets[layer] * 0.98}

    # 单轮再分配：枯竭层的未用预算均分给仍有余量的层
    leftover = sum(t - r["tokens"] for t, r in
                   zip(targets.values(), report.values()) if r["exhausted"])
    donors = [s for s in report if strata[s]]
    if leftover > 0 and donors:
        for layer in sorted(donors):
            picked, got = take(layer, leftover / len(donors))
            chosen.extend(picked)
            report[layer]["tokens"] += got
            report[layer]["docs"] += len(picked)
    return chosen, report


def run(force: bool = False, output_root: Path | None = None, runs_path=None,
        config_paths=None, quality_root: Path | None = None) -> dict:
    """mix 阶段入口。返回 {cached, run_id, rows, tokens, report}。"""
    out_root = Path(output_root) if output_root else DATA_ROOT
    configs = [Path(p) for p in (config_paths or CONFIGS)]
    snaps = snapshot_configs(configs, out_root / f".{STAGE}.configs")
    inputs = latest_outputs(Path(quality_root) if quality_root else DATA_ROOT)
    pol = json.loads(Path(configs[0]).read_text(encoding="utf-8"))
    summary = {}

    def transform(_inputs, work_dir, run_id):
        rows = []
        for path in inputs:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=2000):
                rows.extend(batch.to_pylist())
        chosen, report = stratified_sample(rows, pol, run_id)
        out_path = Path(work_dir) / "candidates" / "part-00000.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(chosen, schema=MIX_EXT_SCHEMA), out_path)
        (Path(work_dir) / "mix_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        summary.update({"rows": len(chosen), "tokens": sum(c["token_count"] for c in chosen),
                        "report": report})
        return [out_path, Path(work_dir) / "mix_report.json"]

    result = run_stage(STAGE, transform, inputs + snaps, out_root,
                       force=force, runs_path=runs_path, pipeline_version=PIPELINE_VERSION)
    result.update(summary if not result["cached"] else {"rows": None})
    return result


def main(argv=None):  # pragma: no cover
    import sys

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps({k: v for k, v in result.items() if k != "output_hashes"},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
