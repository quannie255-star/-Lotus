"""smoke/m1.py — M1 里程碑一行冒烟（DEVLOG M1）

用法: python smoke/m1.py
退出码: 0 = 全部通过 (PASS)；1 = 任一步失败 (FAIL)；2 = 环境性错误（docker 不在）

五步检查（对应 M1 的「可独立跑通」定义）：
  S1 docker 栈: postgres/minio/metastore/spark-master 必须存活（healthy 或 Up）
  S2 单元测试: python -m pytest 全绿
  S3 本地产物: data/raw/wiki/ingest/part-*.parquet 存在且非空
  S4 对象存储: MinIO raw 桶 source=wiki 分区存在 part 文件
  S5 验收 SQL: spark-sql 建外部表 + GROUP BY，wiki 行数 > 0（W1 验收原句）

幂等: 全程只读 + DROP/CREATE IF EXISTS 外部表，可反复执行。
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    """跑子进程，返回 (returncode, stdout+stderr)。docker 相关命令在 repo 根执行。"""
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check_services() -> tuple[bool, str]:
    code, out = _run(["docker", "compose", "ps", "--format", "{{.Service}} {{.Status}}"], 60)
    if code != 0:
        return False, f"docker compose ps 失败: {out.strip()[:200]}"
    required = ["postgres", "minio", "metastore", "spark-master"]
    status = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            status[parts[0]] = parts[1]
    missing = [s for s in required if s not in status]
    down = [s for s in required if s in status and "Up" not in status[s]]
    if missing or down:
        return False, f"未就绪: 缺 {missing} / 未运行 {down}; 实际: {status}"
    return True, f"必需服务存活: {', '.join(required)}"


def check_pytest() -> tuple[bool, str]:
    code, out = _run([PYTHON, "-m", "pytest", "-q"], 300)
    m = re.search(r"(\d+) passed", out)
    if code != 0 or not m:
        return False, f"pytest 退出码 {code}: {out.strip()[-300:]}"
    return True, f"{m.group(1)} passed"


def check_local_parquet() -> tuple[bool, str]:
    parts = sorted((ROOT / "data" / "raw" / "wiki" / "ingest").glob("part-*.parquet"))
    if not parts:
        return False, "data/raw/wiki/ingest/ 下没有 part-*.parquet —— 先跑 python -m ingest.wiki"
    total = sum(p.stat().st_size for p in parts)
    return True, f"{len(parts)} 个 part 文件, {total / 1e6:.0f} MB"


def _minio_creds() -> tuple[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env.get("MINIO_ROOT_USER", "minioadmin"), env.get("MINIO_ROOT_PASSWORD", "")


def check_minio() -> tuple[bool, str]:
    user, password = _minio_creds()
    if not password:
        return False, ".env 里没有 MINIO_ROOT_PASSWORD"
    alias = _run(["docker", "compose", "exec", "minio", "mc", "alias", "set", "q",
                  "http://localhost:9000", user, password], 60)
    if alias[0] != 0:
        return False, f"mc alias set 失败: {alias[1].strip()[:200]}"
    code, out = _run(["docker", "compose", "exec", "minio", "mc", "ls", "q/raw/docs/source=wiki/"], 60)
    if code != 0 or "part-" not in out:
        return False, f"raw 桶 source=wiki 分区无 part 文件（先上传: 见 MANUAL「改输出路径」行）: {out.strip()[:200]}"
    return True, f"MinIO raw/docs/source=wiki/ 存在: {[l.split()[-1] for l in out.splitlines() if 'part-' in l]}"


def check_sql() -> tuple[bool, str]:
    cp = _run(["docker", "compose", "cp", "sql/raw_docs.sql", "spark-master:/tmp/raw_docs.sql"], 60)
    if cp[0] != 0:
        return False, f"复制 SQL 失败: {cp[1].strip()[:200]}"
    code, out = _run(
        ["docker", "compose", "exec", "-T", "spark-master", "/opt/spark/bin/spark-sql",
         "--conf", "spark.sql.hive.manageFilesourcePartitions=false", "-f", "/tmp/raw_docs.sql"],
        600,
    )
    m = re.search(r"wiki\s+(\d+)", out)
    if code != 0 or not m:
        return False, f"spark-sql 未返回 wiki 计数: {out.strip()[-300:]}"
    n = int(m.group(1))
    if n <= 0:
        return False, f"wiki 计数为 {n}"
    pruning = "PartitionFilters" in out
    return True, f"raw_docs GROUP BY: wiki={n} 行; 分区裁剪证据={'有' if pruning else '无(EXPLAIN 未触发, 非失败)'}"


CHECKS = [
    ("S1 docker 栈", check_services),
    ("S2 单元测试", check_pytest),
    ("S3 本地产物", check_local_parquet),
    ("S4 对象存储", check_minio),
    ("S5 验收 SQL", check_sql),
]


def main() -> int:
    print("=" * 56)
    print("M1 SMOKE: 骨架 + wiki 链路")
    print("=" * 56)
    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:  # 环境性错误也算该步失败，不静默
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        mark = "✅" if ok else "❌"
        print(f"{mark} {name}: {detail}")
        results.append(ok)
        if not ok:
            break  # 后续步骤依赖前置（无 SQL 可查时不再空转 spark）
    passed = all(results) and len(results) == len(CHECKS)
    print("-" * 56)
    print(f"SMOKE M1: {'PASS ✅' if passed else 'FAIL ❌'} ({sum(results)}/{len(CHECKS)})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
