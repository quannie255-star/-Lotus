"""dashboard/app.py — 数据质量看板（M10，Streamlit + DuckDB，只读）

四视图：处理漏斗（各层行数）/ 删除归因（changelog 按源×规则）/ 质量分布（五维直方图）/
runs 审计（成功/失败/cached）。全部查询走 DuckDB over 本地 parquet 镜像——不碰 MinIO，
不写任何东西（看板是旁路观测，fail-fast 让位于可用性，见 dev-lessons #004 口试 2）。

用法: streamlit run dashboard/app.py
"""

import json
from pathlib import Path

import duckdb
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CHANGELOG = DATA / "meta" / "changelog.jsonl"
RUNS = DATA / "meta" / "runs.jsonl"

st.set_page_config(page_title="Lotus 濯莲数据看板", layout="wide")
st.title("濯莲 Lotus 数据质量看板")


def _con() -> "duckdb.DuckDBPyConnection":
    return duckdb.connect()


@st.cache_data(ttl=60)
def funnel() -> list:
    """处理漏斗：各层行数（层目录存在才计入）。"""
    rows = []
    for zone, label in (("raw", "raw（采集）"), ("clean", "clean（清洗后）"),
                        ("dedup", "dedup（去重后）"), ("quality", "quality（门禁放行）"),
                        ("mix", "mix（配比候选）"), ("release", "release（发布）")):
        zone_dir = DATA / zone
        if not zone_dir.exists():
            continue
        try:
            n = _con().execute(
                f"SELECT count(*) FROM read_parquet('{zone_dir}/**/*.parquet', "
                f"union_by_name=true, filename=true) WHERE regexp_matches(filename, 'part-')"
            ).fetchone()[0]
            rows.append({"层": label, "行数": n})
        except duckdb.Error:
            rows.append({"层": label, "行数": None})
    return rows


@st.cache_data(ttl=60)
def removal_attribution() -> list:
    """删除归因：changelog 按 源×stage×reason 聚合（无文件返回空）。"""
    if not CHANGELOG.exists():
        return []
    agg: dict = {}
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r.get("source", "?"), r.get("stage", "?"), r.get("reason", "?"))
        agg[key] = agg.get(key, 0) + 1
    return [{"源": s, "阶段": st_, "原因": rs, "条数": n} for (s, st_, rs), n in
            sorted(agg.items(), key=lambda kv: -kv[1])]


@st.cache_data(ttl=60)
def quality_distribution() -> "list":
    """质量分布：quality 层的五维与总分（层未产出返回空）。"""
    qdir = DATA / "quality"
    if not qdir.exists():
        return []
    cols = ["quality_completeness", "quality_uniqueness", "quality_consistency",
            "quality_non_pollution", "quality_length", "quality_score"]
    try:
        df = _con().execute(
            f"SELECT {', '.join(cols)}, source FROM read_parquet('{qdir}/**/*.parquet', union_by_name=true)"
        ).df()
        return df
    except duckdb.Error:
        return None


@st.cache_data(ttl=60)
def runs_audit() -> list:
    """runs 审计：start/finish 对齐为每 run 一行（孤儿 start 标 running）。"""
    if not RUNS.exists():
        return []
    runs: dict = {}
    for line in RUNS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        rid = e.get("run_id")
        rec = runs.setdefault(rid, {"run_id": rid, "stage": e.get("stage", "?"),
                                    "status": "running", "note": ""})
        if e.get("event") == "finish":
            rec.update(status=e.get("status", "?"), note=e.get("note", "") or "")
    return list(runs.values())


@st.cache_data(ttl=60)
def governance_comparison():
    """治理前后指标卡对比（metrics 阶段产物）——控制台的读数面板。"""
    path = DATA / "metrics" / "comparison.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


tab0, tab1, tab2, tab3, tab4 = st.tabs(
    ["治理对比", "处理漏斗", "删除归因", "质量分布", "runs 审计"])

with tab0:
    comp = governance_comparison()
    if comp is None:
        st.info("尚无指标卡——先跑 python -m metrics.run")
    else:
        st.subheader(f"{comp['before']} → {comp['after']}（治理前后全指标）")
        st.dataframe([{"指标": k, **{c: v[c] for c in ("before", "after", "delta", "verdict")}}
                      for k, v in comp["metrics"].items()], use_container_width=True)

with tab1:
    f = funnel()
    st.dataframe(f, use_container_width=True) if f else st.info("尚无层产物")
with tab2:
    a = removal_attribution()
    st.dataframe(a, use_container_width=True) if a else st.info("changelog 尚无记录")
with tab3:
    q = quality_distribution()
    if q is None or len(q) == 0:
        st.info("quality 层尚未产出")
    else:
        dim = st.selectbox("维度", [c for c in q.columns if c != "source"])
        st.bar_chart(q, x=dim, y="quality_score", color="source") if dim else None
        st.dataframe(q.describe(), use_container_width=True)
with tab4:
    r = runs_audit()
    st.dataframe(r, use_container_width=True) if r else st.info("runs 尚无记录")
