"""release/run.py — 版本化发布 + license 执法 + provenance card（M7）

链路：mix candidates → license 政策执法（license_status 不满足发布策略的行剔除并记
changelog）→ data/release/v{semver}/part-*.parquet + provenance_card.yaml + manifest。
数据集 semver：源集合变=minor，配置变=minor，纯重跑=patch（v0.1 起）。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from common import log
from common.idempotent import run_stage
from common.stage import StagedChangeLog, latest_outputs, snapshot_configs

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "release-0.1.0"
DATA_ROOT = ROOT / "data"
STAGE = "release"
STAGING_CHANGELOG = DATA_ROOT / "changelog.staging.jsonl"
CONFIGS = [ROOT / "configs" / "mix_policy.json", ROOT / "configs" / "source_registry.json"]

RELEASE_POLICY = {"allow_license_status": ["permissive"], "note": "unknown/restricted 一律不进发布集（M3 registry 注的执行位）"}


def next_version(current: str, kind: str) -> str:
    """数据集 semver 递增：major=源集合变化，minor=配置/规则变化，patch=纯重跑。

    参数: current — 如 "0.1"；kind — major|minor|patch
    返回: 新版本号字符串
    异常: ValueError — kind 非法或 current 格式错
    幂等: 纯函数
    """
    parts = current.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"版本号格式应为 N.M[.P]: {current}")
    major, minor = int(parts[0]), int(parts[1])
    patch = int(parts[2]) if len(parts) > 2 else 0
    if kind == "major":
        return f"{major + 1}.0"
    if kind == "minor":
        return f"{major}.{minor + 1}"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"kind 必须是 major/minor/patch: {kind}")


def build_provenance_card(version: str, stats: dict, run_id: str) -> str:
    """DPI 风格 provenance card（YAML）。license 表来自 source_registry——单一事实来源。"""
    registry = json.loads((ROOT / "configs" / "source_registry.json").read_text(encoding="utf-8"))
    lines = [
        f"version: {version}", f"run_id: {run_id}",
        f"created_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"pipeline_version: {PIPELINE_VERSION}",
        f"rows: {stats['released']}", f"tokens: {stats.get('tokens', 0)}",
        f"excluded_by_license: {stats['excluded']}",
        "sources:",
    ]
    for source, meta in registry["sources"].items():
        lines += [f"  - source: {source}", f"    license: {meta['license']}",
                  f"    license_status: {meta['license_status']}",
                  f"    commercial_use: {meta['commercial_use']}",
                  f"    tos_url: {meta.get('tos_url') or 'null'}",
                  f"    docs_released: {stats['by_source'].get(source, 0)}"]
    lines.append("removal_stats: " + json.dumps(stats["removal_summary"], ensure_ascii=False))
    lines.append("note: 语料库发布许可 != 页面内容授权清洁（DPI 审计关注点，见 source_registry.notes）")
    return "\n".join(lines) + "\n"


def run(force: bool = False, output_root: Path | None = None, runs_path=None,
        config_paths=None, mix_root: Path | None = None, version: str | None = None) -> dict:
    """release 阶段入口。

    返回: {cached, run_id, version, rows, excluded, card_path}
    异常: UpstreamNotReadyError — mix 未跑
    幂等: 指纹=candidates+配置快照；版本目录按发布版本切分
    """
    out_root = Path(output_root) if output_root else DATA_ROOT
    configs = [Path(p) for p in (config_paths or CONFIGS)]
    snaps = snapshot_configs(configs, out_root / f".{STAGE}.configs")
    inputs = latest_outputs(Path(mix_root) if mix_root else DATA_ROOT, only="mix")

    holder = {"slog": None}
    summary = {}

    def transform(_inputs, work_dir, run_id):
        slog = StagedChangeLog(STAGING_CHANGELOG, run_id=run_id)
        holder["slog"] = slog
        rows = []
        for path in inputs:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=2000):
                rows.extend(batch.to_pylist())

        allowed = RELEASE_POLICY["allow_license_status"]
        kept = [r for r in rows if r.get("license_status") in allowed]
        excluded = [r for r in rows if r.get("license_status") not in allowed]
        for row in excluded:
            slog.log({"doc_id": row["doc_id"], "source": row["source"], "stage": "release",
                      "action": "remove", "reason": "license_restricted",
                      "changed_by": "release:policy", "old_hash": row.get("content_hash"),
                      "detail": f"license_status={row.get('license_status')} 不在 {allowed}"})

        version_dir = Path(work_dir) / f"v{version or '0.1'}"
        version_dir.mkdir(parents=True, exist_ok=True)
        part = version_dir / "part-00000.parquet"
        pq.write_table(pa.Table.from_pylist(kept), part)

        by_source = {}
        for r in kept:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        removal_summary = {}
        try:
            from common.changelog import count_by_reason
            for (stage, reason), n in count_by_reason().items():
                removal_summary[f"{stage}:{reason}"] = n
        except Exception:  # noqa: BLE001 — card 里统计缺失不该阻断发布
            removal_summary = {"note": "changelog 聚合不可用"}
        stats = {"released": len(kept), "excluded": len(excluded),
                 "tokens": sum(r.get("token_count", 0) for r in kept),
                 "by_source": by_source, "removal_summary": removal_summary}
        card = version_dir / "provenance_card.yaml"
        card.write_text(build_provenance_card(version or "0.1", stats, run_id), encoding="utf-8")
        summary.update({"version": version or "0.1", "rows": len(kept),
                        "excluded": len(excluded), "card": str(card)})
        return [part, card]

    result = run_stage(STAGE, transform, inputs + snaps, out_root,
                       force=force, runs_path=runs_path, pipeline_version=PIPELINE_VERSION)
    slog = holder["slog"]
    if result["cached"] or slog is None:
        if slog is not None:
            slog.discard()
    else:
        slog.commit(result["run_id"])
        result.update(summary)
    if result["cached"]:
        result.update(summary)
    return result


def main(argv=None):  # pragma: no cover
    import sys

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
