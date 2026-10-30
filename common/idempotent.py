"""common/idempotent.py — 阶段级幂等（T4）

同一 stage 对同一批输入重跑：
1. 零重复处理：输入指纹没变、产物哈希与 manifest 记录一致 → 直接返回旧产物
2. 输出字节级一致：同样输入两次跑出的文件 sha256 相同（靠确定性 transform + 固定写入方式）
3. 不产生重复 changelog：由 changelog.append_change(dedupe=True) 保证

每个 run 都会在 runs.jsonl 留 start/finish 对（缓存命中也留，note="cached"），
这样"哪些 run 干活了、哪些只是命中缓存"在审计链上是可查的。
"""

import hashlib
import json
import shutil
import uuid
from pathlib import Path

from common import runs


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(paths):
    """输入指纹 = 有序（文件名 + 长度 + 内容 sha256）的链式哈希。

    参数: paths — 输入文件路径的可迭代对象
    返回: 64 位 hex str；同名同内容的多文件输入与单文件输入指纹不同（顺序按路径字符串排序，稳定）
    异常: FileNotFoundError — 任一输入不存在（带路径名，坏输入不静默）
    幂等: 纯函数，无副作用
    注意: 只盖「数据输入」。配置文件的指纹化见 WP-01 §2.1 configs_digest（M4 落地，
          在那之前改配置需手动 force，属已知缺陷，见 DEVLOG M1 遗留）
    """
    digest = hashlib.sha256()
    for path in sorted((Path(p) for p in paths), key=lambda p: str(p)):
        if not path.exists():
            raise FileNotFoundError(f"输入文件不存在: {path}")
        data = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(str(len(data)).encode("ascii"))
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def load_manifest(manifest_path):
    """读 manifest；文件不存在就是空台账。"""
    path = Path(manifest_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(manifest, manifest_path):
    """先写 .tmp 再 replace，避免写一半断电留下半个 JSON。"""
    path = Path(manifest_path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _outputs_intact(final_dir, expected_hashes):
    """验货：产物必须和 manifest 记录的哈希逐个对上。

    只有"输入指纹相同"是不够的——中间可能有人手改了产物、或半截写入，
    那样命中缓存等于把脏数据当成合格产物发出去。
    manifest 键是**相对路径**（M4 修：clean 起产物带 source= 子目录，扁平文件名
    会同名碰撞且哈希路径错位 → 永不命中缓存；合同测试 T1 逮住）。
    """
    if not expected_hashes:
        return False
    for rel, expected in expected_hashes.items():
        path = Path(final_dir) / rel
        if not path.is_file() or _sha256_file(path) != expected:
            return False
    return True


def run_stage(stage, transform, input_paths, output_dir, force=False, runs_path=None, pipeline_version="dev"):
    """幂等执行一个 stage，并把这次执行记进 runs 审计链（DEVLOG M1-D3）。

    参数:
        stage — 阶段名，必须 ∈ configs/reason_registry.json 的 stages
        transform — transform(input_paths, work_dir, run_id) -> 产物路径列表；
                    必须确定性（同输入同字节输出），时间类信息走台账（FirstSeenLedger 范式）
        input_paths — 输入文件列表（进入指纹）
        output_dir — 阶段产物根目录；产物落在 {output_dir}/{stage}/，台账 manifest.json 同级
        force — True 时无视缓存强制重算（重算结果应与缓存字节一致，否则 transform 不确定）
        runs_path — runs.jsonl 路径；None 用默认 data/meta/runs.jsonl
        pipeline_version — 记入 runs 事件的代码/配置版本标识
    返回: {"cached": bool, "run_id": str, "output_dir": Path, "output_hashes": {文件名: sha256}}
    异常:
        ValueError — stage 非法（runs.new_run_id 校验）
        FileNotFoundError — 输入缺失（fingerprint）
        transform 抛出的任何异常 — 原样上抛，但先记 runs finish(failed)
    幂等: 命中条件 = 输入指纹一致 ∧ manifest 存在 ∧ 产物逐文件哈希验货通过（缓存要验货，
          DEVLOG M1-D2）；命中时 transform 零执行、runs 仍记一对 start/finish(note=cached)
    """
    run_id = runs.start(stage, pipeline_version=pipeline_version, path=runs_path)
    try:
        result = _execute(stage, transform, input_paths, output_dir, run_id, force)
    except Exception as exc:  # 失败也留痕：审计链上必须是 failed 而不是"没有这次运行"
        runs.finish(run_id, "failed", note=f"{type(exc).__name__}: {exc}", path=runs_path)
        raise

    runs.finish(run_id, "success", note="cached" if result["cached"] else "executed", path=runs_path)
    result["run_id"] = run_id
    return result


def _execute(stage, transform, input_paths, output_dir, run_id, force):
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    manifest_path = out_root / "manifest.json"
    manifest = load_manifest(manifest_path)
    input_fp = fingerprint(input_paths)
    final_dir = out_root / stage

    entry = manifest.get(stage) or {}
    if (
        not force
        and entry.get("input_fingerprint") == input_fp
        and final_dir.exists()
        and _outputs_intact(final_dir, entry.get("output_hashes", {}))
    ):
        return {"cached": True, "output_dir": final_dir, "output_hashes": entry["output_hashes"]}

    work_dir = out_root / f".{stage}.tmp-{uuid.uuid4().hex[:8]}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    outputs = [Path(p) for p in (transform(input_paths, work_dir, run_id) or [])]

    if final_dir.exists():
        shutil.rmtree(final_dir)
    work_dir.rename(final_dir)

    # 产物键 = 相对 work_dir 的路径（支持子目录布局；wiki 等扁平产物相对路径 == 文件名，向后兼容）
    output_hashes = {}
    for p in outputs:
        rel = p.relative_to(work_dir)
        if (final_dir / rel).is_file():
            output_hashes[str(rel).replace("\\", "/")] = _sha256_file(final_dir / rel)

    manifest[stage] = {"input_fingerprint": input_fp, "output_hashes": output_hashes}
    _write_manifest(manifest, manifest_path)

    return {"cached": False, "output_dir": final_dir, "output_hashes": output_hashes}
