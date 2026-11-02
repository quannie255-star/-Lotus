"""metrics/run.py — 指标卡阶段入口（Tier 0）

对 v0（raw 层）与 v1（release 层）各拍一张质量指标卡，产出治理前后对比卡
data/metrics/comparison.json + 两张原始卡。这是「可编程数据质量控制器」的读数面板：
改任何旋钮 → 重跑管道 → 重拍卡 → diff 就是这次调参的全部效果。
"""

import json
from pathlib import Path

from common import log
from common.idempotent import run_stage
from common.stage import snapshot_configs

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "metrics-0.1.0"
DATA_ROOT = ROOT / "data"
STAGE = "metrics"
STAGING_CHANGELOG = DATA_ROOT / "changelog.staging.jsonl"
CONFIGS = [ROOT / "configs" / "quality_policy.json"]
CHANGELOG = DATA_ROOT / "meta" / "changelog.jsonl"


def _parts_under(zone: str) -> list:
    zone_dir = DATA_ROOT / zone
    if not zone_dir.exists():
        return []
    return sorted(zone_dir.glob("**/part-*.parquet"), key=str)


def run(force: bool = False, output_root: Path | None = None, runs_path=None,
        config_paths=None, changelog_path: Path | None = None) -> dict:
    """拍 v0(raw) 与 v1(release) 的指标卡并对比。

    返回: {cached, run_id, comparison, cards}
    异常: ValueError — raw 与 release 任一无产物（指标卡需要两端读数才有意义）
    幂等: 指纹=两端 part 文件+配置快照；卡片确定性（抽样 seed 固定）
    """
    out_root = Path(output_root) if output_root else DATA_ROOT
    configs = [Path(p) for p in (config_paths or CONFIGS)]
    snaps = snapshot_configs(configs, out_root / f".{STAGE}.configs")
    raw_parts, release_parts = _parts_under("raw"), _parts_under("release")
    if not raw_parts or not release_parts:
        raise ValueError(f"指标卡需要 raw 与 release 两端读数（raw={len(raw_parts)} release={len(release_parts)}）——先跑通管道")

    from metrics.card import compare_cards, compute_card

    summary = {}

    def transform(inputs, work_dir, run_id):
        del inputs
        c_before = compute_card(raw_parts, "v0-raw", changelog_path=Path(changelog_path or CHANGELOG))
        c_after = compute_card(release_parts, "v1-release")
        comp = compare_cards(c_before, c_after)
        out_dir = Path(work_dir)
        (out_dir / "card_v0_raw.json").write_text(
            json.dumps(c_before, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        (out_dir / "card_v1_release.json").write_text(
            json.dumps(c_after, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        (out_dir / "comparison.json").write_text(
            json.dumps(comp, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        summary["comparison"] = comp
        return [out_dir / "card_v0_raw.json", out_dir / "card_v1_release.json",
                out_dir / "comparison.json"]

    result = run_stage(STAGE, transform, raw_parts + release_parts + snaps, out_root,
                       force=force, runs_path=runs_path, pipeline_version=PIPELINE_VERSION)
    result["comparison"] = summary.get("comparison")
    return result


def main(argv=None):  # pragma: no cover
    import sys

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
