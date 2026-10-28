"""⑦ 的守卫测试：requirements 与源字段登记表不许再出现悬空/漂移。"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PACKAGES = {
    "pytest",
    "jsonschema",
    "zhconv",
    "datasets",
    "huggingface_hub",
}


def _registry_rows():
    """把 docs/source_metadata_key_registry.md 的表格解析成 {(source, key): type}。"""
    doc = (ROOT / "docs" / "source_metadata_key_registry.md").read_text(encoding="utf-8")
    rows = {}
    for line in doc.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("source", "---") or set(cells[0]) == {"-"}:
            continue
        rows[(cells[0], cells[1].strip("`"))] = cells[2]
    return rows


def test_every_metadata_key_is_registered():
    """source_registry 里声明过的每个 key，都必须在登记表里出现，且类型一致。"""
    registry = json.loads((ROOT / "configs" / "source_registry.json").read_text(encoding="utf-8"))
    rows = _registry_rows()

    missing, mismatched = [], []
    for source, meta in registry["sources"].items():
        for key, value_type in meta.get("metadata_schema", {}).items():
            if (source, key) not in rows:
                missing.append(f"{source}.{key}")
            elif rows[(source, key)] != value_type:
                mismatched.append(f"{source}.{key}: 表里={rows[(source, key)]}, 配置={value_type}")

    assert not missing, f"未登记的 key: {missing}"
    assert not mismatched, f"类型不一致: {mismatched}"


def test_dangling_doc_reference_is_gone():
    """unified_schema.json 引用的登记表必须真实存在。"""
    schema = (ROOT / "schema" / "unified_schema.json").read_text(encoding="utf-8")
    referenced = set(re.findall(r"docs/[a-z_]+\.md", schema))

    assert referenced, "unified_schema 里应该引用了 docs/ 下的文档"
    for ref in referenced:
        assert (ROOT / ref).exists(), f"悬空引用: {ref}"


def test_requirements_are_pinned():
    """每个依赖都必须 == 固定版本，且覆盖项目实际 import 的三方库。"""
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pinned = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([0-9][0-9A-Za-z.\-]*)", line)
        assert match, f"未固定版本: {line}"
        pinned[match.group(1)] = match.group(2)

    assert REQUIRED_PACKAGES <= set(pinned), f"缺依赖: {REQUIRED_PACKAGES - set(pinned)}"
