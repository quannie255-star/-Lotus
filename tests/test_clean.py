"""M4 测试：common/stage.py 六件套 + clean 规则族 + clean 阶段合同测试（WP-01 §5）"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common import changelog
from common.stage import (UpstreamNotReadyError, StagedChangeLog, configs_digest,
                          latest_outputs, snapshot_configs)

ROOT = Path(__file__).resolve().parent.parent
CLOCK = "2026-10-30T00:00:00+00:00"


# ================================================================ stage 六件套

def test_configs_digest_changes_on_content(tmp_path):
    a = tmp_path / "p.json"
    a.write_text('{"x": 1}', encoding="utf-8")
    d1 = configs_digest([a])
    a.write_text('{"x": 2}', encoding="utf-8")
    assert configs_digest([a]) != d1
    with pytest.raises(FileNotFoundError):
        configs_digest([tmp_path / "missing.json"])


def test_snapshot_configs_byte_stable(tmp_path):
    src = tmp_path / "cfg.json"
    src.write_text("{}{}", encoding="utf-8")
    s1 = snapshot_configs([src], tmp_path / "snap")
    t1 = s1[0].read_bytes()
    snapshot_configs([src], tmp_path / "snap")  # 二次调用不重写
    assert s1[0].read_bytes() == t1


def _make_upstream(root: Path, name: str = "part-00000.parquet", content: bytes = b"x") -> Path:
    stage_dir = root / "raw" / "wiki"
    final = stage_dir / "ingest"
    final.mkdir(parents=True)
    (final / name).write_bytes(content)
    (stage_dir / "manifest.json").write_text(json.dumps(
        {"ingest": {"input_fingerprint": "f", "output_hashes": {name: __import__("hashlib").sha256(content).hexdigest()}}}
    ), encoding="utf-8")
    return stage_dir


def test_latest_outputs_verifies_tamper(tmp_path):
    stage_dir = _make_upstream(tmp_path)
    assert len(latest_outputs(stage_dir)) == 1
    part = stage_dir / "ingest" / "part-00000.parquet"
    part.write_bytes(b"tampered!")  # 篡改
    with pytest.raises(UpstreamNotReadyError, match="不符"):
        latest_outputs(stage_dir)


def test_latest_outputs_missing_manifest(tmp_path):
    with pytest.raises(UpstreamNotReadyError, match="manifest"):
        latest_outputs(tmp_path / "nowhere")


def test_staged_changelog_commit_and_discard(tmp_path):
    global_path = tmp_path / "changelog.jsonl"
    slog = StagedChangeLog(tmp_path / "part.jsonl", global_path=global_path)
    rec = {"doc_id": "d1", "source": "wiki", "stage": "clean", "action": "remove",
           "reason": "too_short", "changed_by": "clean:too_short", "old_hash": "h"}
    slog.log(rec)
    assert slog.pending_count == 1 and not global_path.exists()
    added = slog.commit(run_id="clean-123")
    assert added == 1
    line = json.loads(global_path.read_text(encoding="utf-8").splitlines()[0])
    assert line["run_id"] == "clean-123"          # run_id 由 commit 注入
    assert not slog.part_path.exists()

    # 重放同记录 → dedupe 吸收，零新增
    slog2 = StagedChangeLog(tmp_path / "part.jsonl", global_path=global_path)
    slog2.log(rec)
    assert slog2.commit(run_id="clean-456") == 0

    # discard：失败路径全局零痕迹
    slog3 = StagedChangeLog(tmp_path / "part.jsonl", global_path=global_path)
    slog3.log({**rec, "doc_id": "d2"})
    slog3.discard()
    assert _count(global_path) == 1


def test_staged_changelog_rejects_bad_record(tmp_path):
    slog = StagedChangeLog(tmp_path / "p.jsonl", global_path=tmp_path / "g.jsonl")
    with pytest.raises(ValueError):
        slog.log({"doc_id": "d", "stage": "hack"})  # 缺 required


def _count(p: Path) -> int:
    return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip()) if p.exists() else 0


# ================================================================ 规则族

def _row(content="正常中文正文，长度充分。", cjk=None, chars=None, url=None):
    from common.textstats import text_stats
    s = text_stats(content)
    return {"doc_id": "d", "source": "wiki", "content": content,
            "cjk_char_count": cjk if cjk is not None else s["cjk_char_count"],
            "char_count": chars if chars is not None else s["char_count"],
            "content_hash": "h0", "url": url}


def test_rule_too_short():
    from clean.rules import too_short
    assert too_short(_row(), {"min_cjk_chars": 10, "reason": "too_short"})[0] == "keep"
    verdict = too_short(_row(cjk=3, chars=5), {"min_cjk_chars": 10, "reason": "too_short"})
    assert verdict == ("drop", "too_short")


def test_rule_lang_ratio():
    from clean.rules import lang_ratio
    cfg = {"min_han_ratio": 0.25, "reason": "lang_mismatch"}
    assert lang_ratio(_row(), cfg)[0] == "keep"
    assert lang_ratio(_row(content="hello world this is english text only", cjk=0, chars=34), cfg) == \
        ("drop", "lang_mismatch")


def test_rule_mojibake():
    from clean.rules import mojibake
    cfg = {"max_fffd_per_kchar": 2.0, "reason": "mojibake"}
    assert mojibake(_row(), cfg)[0] == "keep"
    bad = _row(content="乱码\ufffd\ufffd\ufffd正文" * 30)
    assert mojibake(bad, cfg) == ("drop", "mojibake")


def test_rule_boilerplate_link_heavy():
    from clean.rules import boilerplate
    cfg = {"max_link_density": 0.30, "max_short_line_ratio": 0.80, "reason": "boilerplate_heavy"}
    spam = _row(content="点击 https://example.com/aaa/bbb/ccc/ddd/eee/fff/ggg 链接 "
                        "https://example.com/xxx/yyy/zzz/uuu/vvv/www 更多")
    assert boilerplate(spam, cfg) == ("drop", "boilerplate_heavy")
    assert boilerplate(_row(), cfg)[0] == "keep"


def test_rule_pii_redact_transform_with_meta():
    from clean import pii
    from clean.rules import pii_redact
    cfg = {"reason": "pii_redacted"}
    id_card = "110101199003077758"  # 合法校验位
    row = _row(content=f"联系方式见正文，身份证 {id_card}，请勿外传。")
    verdict = pii_redact(row, cfg)
    assert verdict[0] == "transform"
    new_row, reason, action, meta = verdict[1], verdict[2], verdict[3], verdict[4]
    assert action == "redact" and reason == "pii_redacted"
    assert id_card not in new_row["content"]
    assert meta["pii_types"] == ["id_card"]
    assert meta["old_hash"] == "h0" and meta["new_hash"] != "h0"
    # 掩码策略保长（3+4 可见 + 星号补齐）：长度不变、内容必变（M4 实测校准的断言语义）
    assert meta["after_chars"] == meta["before_chars"]


def test_rule_url_norm():
    from clean.rules import url_norm
    cfg = {"strip_params_prefix": ["utm_", "spm"], "reason": "url_normalized"}
    row = _row(url="HTTP://Example.ORG/page/?utm_source=x&b=2&a=1#frag")
    verdict = url_norm(row, cfg)
    assert verdict[0] == "transform"
    assert verdict[1]["canonical_url"] == "http://example.org/page/?a=1&b=2"


# ================================================================ 阶段合同（WP-01 T1/T2/T3/T4）

@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """微型双源环境：raw/wiki + raw/oscar 各 1 part + 可改的 clean_policy 副本。"""
    import shutil

    from common.first_seen import FirstSeenLedger
    from ingest.spine_schema import SPINE_PARQUET_SCHEMA

    ledger = FirstSeenLedger(tmp_path / "fs.json", clock=lambda: CLOCK)
    rows = {
        "wiki": [
            {"doc_id": f"w{i}", "schema_version": "t", "source": "wiki", "source_id": str(i),
             "url": f"https://zh.wikipedia.org/wiki/x?utm_source=t{i}", "canonical_url": None,
             "content_hash": f"h{i}", "content": "这是完全正常的中文正文段落。" * 3,
             "content_format": "plain_text", "raw_uri": None, "original_encoding": None,
             "language": "zh", "char_count": 0, "cjk_char_count": 0, "utf8_byte_count": 0,
             "source_timestamp": None, "timestamp_source": "none",
             "first_seen_at": CLOCK, "genre": "wiki", "license": "CC BY-SA 4.0",
             "license_status": "permissive", "commercial_use": "allowed",
             "source_metadata": {"page_title": "t", "publisher": None, "section": None}}
            for i in range(3)
        ],
        "oscar": [
            # 一条好 + 一条 too_short + 一条 lang_mismatch
        ],
    }
    rows["oscar"] = [dict(rows["wiki"][0], doc_id="o1", source="oscar", source_id="1",
                          content="短", cjk_char_count=1),
                     dict(rows["wiki"][0], doc_id="o2", source="oscar", source_id="2",
                          content="english only text here", cjk_char_count=0)]
    import hashlib as _h
    for source, rs in rows.items():
        for r in rs:
            from common.textstats import text_stats
            s = text_stats(r["content"])
            r.update(char_count=s["char_count"], cjk_char_count=s["cjk_char_count"],
                     utf8_byte_count=s["utf8_byte_count"])
        stage_dir = tmp_path / "raw" / source
        final = stage_dir / "ingest"
        final.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rs, schema=SPINE_PARQUET_SCHEMA),
                       final / "part-00000.parquet")
        data = (final / "part-00000.parquet").read_bytes()
        (stage_dir / "manifest.json").write_text(json.dumps(
            {"ingest": {"input_fingerprint": "f", "output_hashes": {
                "part-00000.parquet": _h.sha256(data).hexdigest()}}}), encoding="utf-8")

    # 配置副本（改它测 T3）
    policy_copy = tmp_path / "clean_policy.json"
    shutil.copy(ROOT / "configs" / "clean_policy.json", policy_copy)
    registry_copy = tmp_path / "reason_registry.json"
    shutil.copy(ROOT / "configs" / "reason_registry.json", registry_copy)

    monkeypatch.setattr("clean.run.RAW_ROOT", tmp_path / "raw")
    monkeypatch.setattr("clean.base.POLICY_PATH", policy_copy)
    monkeypatch.setattr("clean.run.STAGING_CHANGELOG", tmp_path / "staging.jsonl")
    # 关键隔离：StagedChangeLog 的全局默认路径指向仓库真实 changelog——不隔离的话
    # 测试会写穿仓库账本，并与并发运行互踩计数（M4 实测教训：单跑过、全套挂 = 状态泄漏）
    monkeypatch.setattr("common.changelog.CHANGELOG_PATH", tmp_path / "changelog.jsonl")
    # base.policy 的 lru_cache 绑定旧路径 → 用读副本的加载函数替换
    from clean import base
    monkeypatch.setattr(base, "policy", lambda: json.loads(policy_copy.read_text(encoding="utf-8")))

    ledger.commit()
    return tmp_path


def _run_clean(env, **kw):
    from clean import run as clean_run
    return clean_run.run(output_root=env / "data", runs_path=env / "runs.jsonl",
                         config_paths=[env / "clean_policy.json", env / "reason_registry.json"],
                         **kw)


def test_T1_clean_is_idempotent(clean_env):
    first = _run_clean(clean_env)
    assert first["cached"] is False
    assert first["rows"] == 3                     # wiki 3 保留；oscar 两条坏行（短/纯英文）被删
    assert first["changelog_added"] == 2
    second = _run_clean(clean_env)
    assert second["cached"] is True and second["rows"] is None
    events = [json.loads(l) for l in (clean_env / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["note"] for e in events if e["event"] == "finish"] == ["executed", "cached"]


def test_T2_force_is_byte_identical(clean_env):
    first = _run_clean(clean_env)
    forced = _run_clean(clean_env, force=True)
    assert forced["cached"] is False
    assert forced["output_hashes"] == first["output_hashes"]


def test_T3_config_change_forces_recompute(clean_env):
    _run_clean(clean_env)
    policy = json.loads((clean_env / "clean_policy.json").read_text(encoding="utf-8"))
    policy["too_short"]["min_cjk_chars"] = 0      # 阈值变了 → 「短」行放行，产物必须变
    policy["boilerplate"]["max_short_line_ratio"] = 1.01  # 单行短文会被 boilerplate 补刀，一并放开
    (clean_env / "clean_policy.json").write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    result = _run_clean(clean_env)
    assert result["cached"] is False              # 修掉了 M1 遗留：指纹现在含配置
    assert result["rows"] == 4                    # wiki 3 + oscar 的「短」行


def test_T4_crash_leaves_no_global_changelog(clean_env, monkeypatch):
    from clean import base
    real = base.iter_clean

    def exploding(docs, rules, slog, **kw):
        rows_iter, stats = real(docs, rules, slog, **kw)
        def gen():
            yield from rows_iter
            raise RuntimeError("transform 中途崩溃")
        return gen(), stats

    monkeypatch.setattr("clean.base.iter_clean", exploding)  # run() 内局部导入，patch 源头
    with pytest.raises(RuntimeError, match="中途崩溃"):
        _run_clean(clean_env)
    # 账实分离：runs 记 failed；全局 changelog 零新增；产物目录无 clean/
    events = [json.loads(l) for l in (clean_env / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["status"] == "failed"
    assert not (clean_env / "data" / "meta" / "changelog.jsonl").exists()
    assert not (clean_env / "data" / "clean").exists()
