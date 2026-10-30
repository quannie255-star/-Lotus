"""common/stage.py — WP-01 阶段契约的实装（M4）（DEVLOG M4-D1）

六件套：configs_digest / snapshot_configs / latest_outputs / UpstreamNotReadyError /
StagedChangeLog / run_pipeline。契约全文见 docs/wp-01-stage-protocol.md §2，
本文只写行为不重复论证。核心动机两条：
1. 配置进指纹（修 M1 遗留缺陷：改配置必须重算）；
2. changelog 两段式提交（账实一致：失败 run 在全局 changelog 零痕迹，尸检在 runs）。
"""

import hashlib
import json
import shutil
from pathlib import Path

from common import changelog as changelog_mod


class UpstreamNotReadyError(Exception):
    """上游阶段产物缺失/未完成/被篡改。属性 .stage_dir 指向上游目录。"""

    def __init__(self, stage_dir: Path, reason: str):
        self.stage_dir = stage_dir
        super().__init__(f"上游未就绪 {stage_dir}: {reason}")


def configs_digest(config_paths: list) -> str:
    """配置指纹：sha256( sorted(relpath + 文件字节) 串接 )。

    参数: config_paths — 配置文件路径列表
    返回: 64 位 hex
    异常: FileNotFoundError — 任一配置缺失（带路径）
    幂等: 纯函数
    """
    digest = hashlib.sha256()
    for path in sorted((Path(p) for p in config_paths), key=str):
        data = path.read_bytes()
        digest.update(str(path).encode("utf-8"))
        digest.update(str(len(data)).encode("ascii"))
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def snapshot_configs(config_paths: list, stable_dir: Path) -> list:
    """把配置拷进 stable_dir（快照=指纹锚+provenance 双用途）。

    参数: config_paths — 源配置；stable_dir — 快照目录（各阶段 output 根下的 .configs/）
    返回: 快照文件路径列表（并入 run_stage 的 input_paths 参与指纹）
    异常: FileNotFoundError — 源缺失；OSError — 写盘失败
    幂等: 同名且内容一致不重写（快照自身字节稳定，否则它破坏幂等）
    """
    stable_dir = Path(stable_dir)
    stable_dir.mkdir(parents=True, exist_ok=True)
    snaps = []
    for src in sorted((Path(p) for p in config_paths), key=str):
        dest = stable_dir / src.name
        data = src.read_bytes()
        if dest.exists() and dest.read_bytes() == data:
            snaps.append(dest)
            continue
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        snaps.append(dest)
    return snaps


def latest_outputs(stage_dir: Path) -> list:
    """读上游 manifest，验货后返回 part 文件清单。

    参数: stage_dir — 上游 output 根（其下有 manifest.json 与 {stage}/ 产物目录）
    返回: 存在且通过验货的产物路径列表（只挑 part-*.parquet）
    异常: UpstreamNotReadyError — manifest 缺失/损坏/产物被动过
    幂等: 只读
    """
    from common.idempotent import load_manifest  # 局部导入避免环

    stage_dir = Path(stage_dir)
    manifest_path = stage_dir / "manifest.json"
    if not manifest_path.exists():
        raise UpstreamNotReadyError(stage_dir, "没有 manifest.json——上游从未成功跑过")
    try:
        manifest = load_manifest(manifest_path)
    except json.JSONDecodeError as exc:
        raise UpstreamNotReadyError(stage_dir, f"manifest 损坏: {exc}") from exc
    if not manifest:
        raise UpstreamNotReadyError(stage_dir, "manifest 为空")

    outputs = []
    for stage, entry in manifest.items():
        final_dir = stage_dir / stage
        for name, expected in (entry.get("output_hashes") or {}).items():
            path = final_dir / name
            if not Path(name).name.startswith("part-"):
                continue
            if not path.is_file():
                raise UpstreamNotReadyError(stage_dir, f"{stage}/{name} 产物文件丢失")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise UpstreamNotReadyError(
                    stage_dir, f"{stage}/{name} 哈希与 manifest 不符（被篡改/半截写）")
            outputs.append(path)
    if not outputs:
        raise UpstreamNotReadyError(stage_dir, "manifest 里没有任何 part-* 产物")
    return sorted(outputs, key=str)


class StagedChangeLog:
    """两段式变更日志（范式同 FirstSeenLedger）：part 文件 → 成功后 commit。

    参数: global_path — 全局 changelog.jsonl；part_path — 本次 run 的暂存 part 文件
    方法: log(record) 校验并暂存；commit(run_id) 逐条 dedupe 追加进全局（可安全重试）；
          discard() 删除 part（失败路径）
    异常: log 校验失败抛 ValueError（坏记录连 part 都不进）；commit 遇 I/O 异常上抛
    幂等: commit 重试安全（dedupe 键吸收重复）；账实一致定义见 WP-01 §2.3
    """

    def __init__(self, part_path: Path, global_path: Path = None, run_id: str | None = None):
        self.part_path = Path(part_path)
        self.global_path = Path(global_path) if global_path else changelog_mod.CHANGELOG_PATH
        self.run_id = run_id
        self._count = 0

    def log(self, record: dict) -> None:
        """校验（含 run_id 形状的合并探测）后暂存。构造时给了 run_id 则注入进记录。"""
        record.setdefault("changed_at", changelog_mod.now_iso())
        if self.run_id:
            record["run_id"] = self.run_id
        # schema 要求 run_id 必填；暂存记录可能由 commit 注入——用合并形状做校验
        changelog_mod.validate({**record, "run_id": record.get("run_id", "pending-injection")})
        self.part_path.parent.mkdir(parents=True, exist_ok=True)
        with self.part_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._count += 1

    def commit(self, run_id: str) -> int:
        """把 part 逐条以 dedupe=True 追加进全局。返回真实新增条数（行数差口径，
        避免逐条判定去重的 O(n²)）。commit 中途崩溃可整体重试——dedupe 键吸收重复。"""
        if not self.part_path.exists():
            return 0
        before = _count_lines(self.global_path)
        with self.part_path.open(encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if not text:
                    continue
                record = json.loads(text)
                record.setdefault("run_id", run_id)
                changelog_mod.append_change(record, path=self.global_path, dedupe=True)
        added = _count_lines(self.global_path) - before
        self.part_path.unlink(missing_ok=True)
        return added

    def discard(self) -> None:
        self.part_path.unlink(missing_ok=True)

    @property
    def pending_count(self) -> int:
        return self._count


def _count_lines(path: Path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    with p.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def run_pipeline(stage_calls: list, *, on_fail: str = "raise") -> list:
    """薄驱动：按序执行阶段闭包（DEVLOG M4-D1 / WP-01 D5）。

    参数: stage_calls — 每项是零参调用返回 StageResult 的闭包；on_fail: raise|skip
    返回: 各阶段结果列表（skip 模式下失败项为 {"failed": str(exc)}）
    异常: on_fail=raise 时第一个异常原样上抛
    幂等: 驱动本身无状态；每阶段自幂等（run_stage）
    """
    results = []
    for call in stage_calls:
        try:
            results.append(call())
        except Exception as exc:  # noqa: BLE001 — skip 模式需要收集
            if on_fail == "raise":
                raise
            results.append({"failed": f"{type(exc).__name__}: {exc}"})
    return results
