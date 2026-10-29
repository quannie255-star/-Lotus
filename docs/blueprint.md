# datalens 开发蓝图（Blueprint v1.0）

> 定位：全项目的**契约层文档**——架构、每个模块的函数签名与输入输出、实验协议、有效性验证方案。
> 它回答「建什么、接口长什么样、怎么验收」，不回答「怎么实现」——实现是 owner 的活。
>
> 状态标记：✅ 已完成并验证 · 🟨 部分完成 · ⬜ 未开始 · ⚠️ 依赖未决事项（数据实况/选型），落地前必须先关
>
> 修订规则：本文档随真实数据撞出来的事实修订（如 OSCAR 行结构），每次修订升版本号并记录原因。
> 当前待决的最大未知数：OSCAR-2201 meta/content 两段式结构（等 FlClash 开启后实测）。

---

## 0. 项目宪法（所有模块共守的七条不变量）

任何模块的实现偏离这七条，视为 bug 而非风格问题：

1. **原始层不可变**：raw 一条不删不改；一切删除发生在派生层，且只通过「不写入下一层 + 记 change log」表达；
2. **一表一阶段**：每阶段读一张表、写一张新表 + 审计记录；阶段间只通过表耦合，不通过代码调用耦合；
3. **确定性身份**：`doc_id = sha256(source + ':' + source_id)`，键材料永不扩展，ID 一经发布永不复算；
4. **字节级幂等**：同样输入跑两遍，产物哈希一致——任何 now()、随机数、字典序漂移不得进入数据行（first_seen 台账就是为此而生）；
5. **每条变更可归因**：change log 必含 run_id；runs.jsonl 与 manifest 是运行侧的账本，数据行只承载事实；
6. **事实单一来源**：license/genre/format/reason/threshold 等一律来自 configs 注册表，业务代码里出现这些字面量即接缝泄漏（有元测试守卫）；
7. **坏数据宁可停或隔离，绝不静默**：缺主键 fail-fast；脏行 quarantine 成为一等公民，可查询、可复核、可放回。

---

## 1. 整体架构

### 1.1 数据流主线

```
【外部源】                                存储布局（MinIO）
oscar(HF+S3,脏) ─┐                      s3a://raw/docs/source=xxx/     ← raw 层（不可变，hive 分区）
wiki(HF,干净)   ─┼─ ingest ─ parse ─┐    data/ 本地镜像（调试+指纹）
news(待选型)    ─┤                  ├→ clean → dedup → quality → mix → release
pdf(政府文档)   ─┘                  ┘    每阶段一张 parquet 表 + change log + runs 审计
                                              ↑
Kafka(增量模拟) → Spark Structured Streaming ┘（旁路，W7，汇入 raw）
                                              ↓
                          downstream（困惑度闭环）→ 反馈 mix 配比/quality 阈值
```

### 1.2 阶段与表（现状 → 目标）

| 阶段 | 输入 | 输出表（W1-W6 为 Parquet 外部表） | 状态 |
|---|---|---|---|
| ingest | HF/direct 下载 | `raw/docs/source={wiki,oscar,news,pdf}/` | wiki ✅，其余 ⬜ |
| parse | raw 行的 raw_uri | `parsed/`（仅 pdf/html 需要；wiki/oscar 已是文本） | ⬜ |
| clean | raw | `clean/docs/` + change log | 规则框架 ⬜，pii ✅ |
| dedup | clean | `dedup/docs/` + 签名表 + change log | ⬜（纯 py W3，Spark W4） |
| quality | dedup | `quality/docs/`（追加分数列）+ 门禁报告 | ⬜ |
| mix | quality | `release/candidates/` | ⬜ |
| release | candidates | `s3://release/v{x}/` + provenance card | ⬜ |
| downstream | release v0(基线)/v1 | 对比报告（Δppl + 消融） | ⬜ |

### 1.3 横切层

- **common/**（已建）：`ids`（身份/哈希）、`runs`+`changelog`（审计）、`idempotent`（run_stage）、`first_seen`（时间台账）、`textstats`（字数统计）——全部模块站在其上；
- **configs/**：source_registry / reason_registry / text_normalization(v1.0 ✅) / url_canonicalization / dedup_policy / quality_weights / mix_policy（后四份 ⬜，随各自模块定稿）；
- **schema/**：unified(v0.3.x ✅) / removal_log(✅) / dedup_signature(✅)；
- **dashboard/**：Streamlit 只读看板（漏斗/删除归因/质量分布/污染/runs 审计）；
- **Airflow**：`dags/corpus_pipeline.py` 把 ingest→release 串成带重试的 DAG（W6 末接入）。

### 1.4 基础设施（✅ 已全部 boot 验证，见 dev-lessons #004/#005）

postgres(元数据) · minio(数据) · metastore(thrift 目录) · spark master/worker(s3a 已通) · airflow · kafka。
已知取舍：metastore 无 S3A 凭据 → 外部表用文件系统分区发现（`manageFilesourcePartitions=false`）；eventLog-on-s3a 暂关（backlog）。

---

## 2. 模块契约（函数签名即接口，实现者不得改签名、可加私有函数）

### 2.1 common/（既有扩展）

```python
# common/upload.py ⬜ —— 统一的上传通道（免新增 SDK 依赖，复用容器内 mc）
def mc_cp(local_paths: list[Path], target: str) -> None:
    """docker compose exec minio mc cp <local> <target>；失败抛异常带 mc 原始输出。
    target 形如 q/raw/docs/source=wiki/（q 为已配置的 alias）。"""

# common/validate.py ⬜ —— 行级 schema 校验（golden 测试与适配器运行时共用）
def validate_spine_row(row: dict) -> None:
    """按 schema/unified_schema.json 的 spine 节校验：必填齐全、无未声明键、类型匹配。
    违规抛 ValueError 并指名违规键。注意性能：批量场景校验首尾批 + 抽样批即可。"""
```

### 2.2 ingest/（适配器家族，wiki ✅ 为参照实现）

**家族契约**（duck-typing，照 wiki.py 的形状）：

```python
SOURCE: str                                   # registry 键
def download(n: int) -> list[Path]            # 下载级幂等（hf_hub_download / requests+断点）
def iter_docs(src) -> Iterator[dict]          # 流式产出 spine 行；坏行按 quarantine 策略
def run(...) -> dict                          # run_stage 包装：download→map→本地 parquet
```

```python
# ingest/sampling.py ⬜ —— 确定性抽样（T6 设计已定，落地成代码）
def sample_shards(all_shards: list[str], k: int, seed: int) -> list[str]:
    """sha256(seed + shard_name) 排序取前 k —— 整群抽样，与目录顺序无关。"""
def write_sample_manifest(selected, formula: str, path: Path) -> None:
    """选中清单+种子+公式落盘；该 manifest 即 run_stage 的输入指纹（幂等白拿）。"""

# ingest/oscar.py ⚠️ —— 接口稳定，映射表等数据实况
# 已知事实：HF 仓只含 *_meta(jsonl.gz)，正文在 OSCAR S3；取文件需 huggingface.co 直连（开 FlClash）
# 待实测后定：meta 行 schema、正文指针形态、source_id 用 sha256(正文) 还是 meta 内键、
#            language 字段是否可信（OSCAR 自带语言判定）、与 zh_meta 分片的对应关系。
# 坏行策略（已定）：quarantine 一等公民 + 分片失败率护栏（阈值入 configs，默认 5%）。

# ingest/news.py ⚠️ —— 源选型未决（见 §6 决策点 N2）

# ingest/pdf.py ⬜ —— 政府文档源
# 清单 configs/pdf_sources.json（白皮书/统计公报，人工维护 URL 列表）
# download：requests + 校验(C-T application/pdf + magic bytes %PDF)
# source_id = sha256(原始文件字节)（无稳定主键，registry 已声明）
# content = MinerU 提取的 markdown（parse 作为库在适配器内调用，见 2.3 决策注）
```

### 2.3 parse/

```python
# parse/pdf_extractor.py ⬜
def extract_pdf(pdf_path: Path) -> "ExtractResult":
    """ExtractResult = dataclass(markdown: str, page_count: int, engine_version: str,
       warnings: list[str])。MinerU 为主引擎；文本层完好的 PDF 先走 PyMuPDF 轻量路径，
       扫描件才进 OCR（成本悬崖，见决策点 N3）。失败不抛——返回 warnings 由调用方 quarantine。"""

# parse/html_extractor.py ⬜（砍单优先级低于 pdf）
def extract_html(html_path: Path) -> "ExtractResult": ...   # trafilatura，输出 markdown
```

> 决策注：pdf 源在 ingest 适配器内调用 parse（parse 作为库），而不是独立 pass 一遍 raw——
> 理由：spine 的 content 必填，pdf 行的 content 只能来自提取；两阶段方案（先空 content 后填）
> 会破坏「spine 行即完整事实」。这个取舍写进 ADR。

### 2.4 clean/（规则框架 + 规则族）

```python
# clean/base.py ⬜ —— 规则框架（核心：规则是数据，不是代码分支）
RuleVerdict = ("keep", None) | ("drop", reason) | ("transform", new_row, reason, action)

def load_rules(reason_registry, policy_cfg) -> list[Rule]:
    """规则按 reason_registry 登记装配；每条规则携带自己的阈值（来自 configs）。"""

def apply_rules(docs: Iterator[dict], rules: list[Rule],
                run_id: str, writer: ChangeLogWriter) -> Iterator[dict]:
    """逐行过规则链；drop → changelog(action=remove)；transform(pii) → changelog(action=redact,
    含 before/after_char_count + old/new_hash)。聚合统计（每规则×每源的删除率）返回侧信道。"""

# clean/rules/*.py ⬜（每条规则一个纯函数，全部可单测）
length.py:     min_cjk 过滤（阈值 per genre 入 configs）
lang.py:       语言检测器包装 → 非目标语言/置信度低 → drop(lang_mismatch) ⚠️检测器选型 N4
mojibake.py:   乱码探测（U+FFFD 密度、chardet 置信度、可修复性）→ drop 或 transform
boilerplate.py:样板占比（导航词表命中率、超链接密度、重复行率）→ drop
pii.py:        ✅ 已实现 detect/redact，接入框架为 transform 规则
url_norm.py:   canonical_url 回填（transform，规则读 configs/url_canonicalization.json）
```

### 2.5 dedup/

```python
# dedup/exact.py ⬜
def exact_dedup(docs, policy: dict) -> ExactReport:
    """按 normalized_content_hash 分组；簇内选 canonical（policy: source 可信度 > 时间早 > 内容长）；
    非代表行 → changelog(reason=exact_dup, ref_doc_id=canonical)。"""

# dedup/minhash.py ⬜（W3 纯 Python 版，datasketch）
def shingles(text: str, unit: str, n: int) -> set[str]: ...        # 中文默认 char 3-gram
def compute_signatures(docs, num_perm: int) -> SignaturesTable: ... # 落 dedup_signature_schema
def lsh_candidates(sigs, bands: int) -> list[set[doc_id]]: ...     # MinHashLSH，band 参数来自 configs
def verify_pairs(cands, threshold: float) -> list[Cluster]: ...     # 候选对算真实 Jaccard 再判（防 LSH 误报直删）
def near_dedup(docs, policy) -> NearDedupReport:
    """并查集成簇 → canonical → changelog(reason=near_dup, similarity, ref_doc_id, dup_cluster_id)。"""

# dedup/report.py ⬜ —— 跨源重复矩阵（本项目亮点 1 的载体）
def cross_source_overlap(clusters) -> pd.DataFrame:
    """行列=source，单元格=簇的跨源共现计数 → 直接回答「合并集中 __% 重复来自跨源」。"""

# dedup/spark_minhash.py ⬜（W4）：同契约的 Spark 实现（签名 udf 广播 datasketch 或手写 band 分组）
# ray_dedup/ray_minhash.py ⬜（W5）：Ray actors 版；与 spark 版共用 bench 协议
# dedup/bench.py ⬜：双引擎对比 harness（固定输入、固定核数、输出耗时+峰值内存+结果一致性 diff）
```

### 2.6 quality/

```python
# quality/dims.py ⬜ —— 五核心维（0~1，越高越好）
score_completeness(row) -> float    # 空段率、截断特征（句尾标点缺失率）
score_uniqueness(row) -> float      # 消费 dedup 签名：与最近邻 Jaccard 距离映射
score_consistency(row) -> float     # mojibake 残留、简繁混排比、脚本混排比
score_non_pollution(row) -> float   # 消费 contamination 命中数，-log 映射
score_length(row, genre) -> float   # 分体裁分桶校准（wiki 段落长度 ≠ 新闻）
# 进阶三维（允许 v1 返回 None 并在门禁处豁免）
score_not_ai_generated(row) -> float | None   # 代理：小模型困惑度分位 + 套话密度
score_non_template(row) -> float | None
score_no_pii(row) -> float                    # 消费 clean 阶段 pii 命中记录

# quality/contamination.py ⬜（W6）
def build_ngram_index(benchmarks: list[Path], n: int = 13) -> set[int]:
    """评测集(CEval/CMMLU/GSM8K 题面)→n-gram 哈希集合；n 取 13 与 GPT-3 论文口径一致。"""
def check_contamination(text: str, index) -> list[hit]: ...

# quality/gate.py ⬜
def gate(row, weights_cfg, thresholds_cfg) -> ("pass" | "hold" | "quarantine"):
    """加权总分 + 维度下限双门禁；hold=不进 mix 但不删（可被配比策略回头捞）。"""
# 变异测试（tests/mutations/）：注入乱码/评测集原文/PII 密集文本 → 必须拦住，拦不住测试变红

# quality/judge.py ⬜ —— LLM 判官 + 蒸馏 + 审计（方案早前已定）
sample_for_judging(pool, k, seed) -> list[doc_id]
judge_batch(docs, rubric_cfg) -> dict[doc_id, score]        # ⚠️后端选型 N5（便宜 API vs 本地 ollama）
distill(scored_pairs) -> "QualityClassifier"                 # ⚠️fasttext Windows 轮子风险 N6
audit_agreement(gate_verdicts, judge_scores) -> float        # kappa，写入门禁报告
```

### 2.7 mix/

```python
# mix/policy.py ⬜ —— 读 configs/mix_policy.json
# 结构：{strata: [source×genre×quality_tier], target_ratio: {...}, token_budget: int, seed: int}

# mix/tokenizer.py ⬜
def token_count(text: str, tokenizer_name: str) -> int: ...   # transformers 按需缓存；Qwen 分词器经镜像拉

# mix/sampler.py ⬜ —— 确定性分层抽样（不放回）
def stratified_sample(rows, policy, token_budget, seed) -> (chosen, MixReport):
    """逐层按 target_ratio 分 token 预算 → 层内按 quality_score 降序候选 → 种子洗牌后取满预算；
    MixReport 记每层 kept/target/不足原因（层枯竭/预算尽）。chosen 行附 mix_batch_id + sampling_weight。"""
```

### 2.8 release/

```python
# release/versioning.py ⬜ —— 数据集 semver：源集合变=大版本，配置变=小版本，重跑=补丁
def next_version(current: str, change_kind: str) -> str: ...

# release/provenance.py ⬜
def build_card(run_ids, snapshot_hashes, configs_digest, code_version,
               license_table, removal_stats) -> str:
    """YAML provenance card（DPI 风格）：每个源的 license/条款/条数/哈希、
    各阶段删除统计、全部配置摘要、代码版本。随数据集一起发布。"""

# release/publish.py ⬜
def publish(candidates: Path, version: str, bucket: str = "release") -> Manifest:
    """校验 license 政策（license_status 不满足发布策略的行剔除并记 changelog）→
    拷贝到 s3://release/v{x}/ → 生成 manifest（文件清单+哈希）。可选 flag：上传 HF 数据集。"""
```

### 2.9 downstream/（唯一核心指标的家）

```python
# downstream/evalset.py ⬜ —— 冻结评测集（协议的支点）
def build_evalset(clean_pool, k: int, seed: int) -> Path:
    """从 clean 样本中分层抽取 k 条文本，冻结成 evalset.parquet + manifest。
    评测集一旦发布永不改（和 doc_id 同一纪律）——它是所有 Δppl 的公共参照系。"""

# downstream/ppl.py ⬜ ⚠️后端选型 N7（kenlm vs transformers tiny-LM）
def train_lm(corpus_path: Path, order: int = 5) -> "LM": ...
def eval_ppl(model, evalset: Path) -> float: ...
# 协议（FineWeb 式，防两个经典坑）：
#   ① 混淆因子：不能「在 A 上算 ppl vs 在 B 上算 ppl」——文本分布不同，ppl 不可比。
#      正确姿势：用 A 训 tiny-LM₁、用 B 训 tiny-LM₂，**两个模型在同一冻结评测集上**比 ppl。
#   ② 报告口径：bootstrap(评测集重采样) 给 Δppl 置信区间，不给裸数字。

# downstream/ablation.py ⬜ —— 归因（面试的核心弹药）
def run_ablation() -> dict:
    """四臂对比：raw / clean-only / dedup-only / full —— 同协议同评测集。
    回答「增益来自清洗还是去重还是配比」，而不是笼统的「治理有用」。"""

# downstream/report.py ⬜ —— 生成 markdown 报告（占位符纪律：没跑出的数字不进报告）
```

### 2.10 stream/（W7，砍单顺序第 4 位）

```python
# stream/producer.py ⬜ —— 用留出的分片尾部模拟增量
def produce(shard_paths, topic="corpus-ingest", interval_s=1.0) -> None: ...
# stream/stream_job.py ⬜ —— Structured Streaming：kafka→foreachBatch→raw/docs/source=stream-sim/
#   幂等保证：doc_id 确定性 → 重放数据被下游 dedup 自然折叠（这个设计要在 README 里讲出来）
```

### 2.11 dags/ 与 sql/ 与 dashboard/

```
dags/corpus_pipeline.py ⬜：ingest_{src} 并行 → clean → dedup → quality → mix → release；
  每任务 retries=2 + 指数退避；任务= BashOperator 调 python -m <module>（模块自带幂等，
  重试语义 = run_stage 的 cached/force，与 Airflow 的重试天然咬合——这个对齐关系写进 DAG 注释）。
sql/ ⬜：raw_docs.sql ✅ 之外补 数据画像（长度分位/体裁分布/license 分布/各阶段行数漏斗）。
dashboard/app.py ⬜：Streamlit 五页（漏斗/删除归因/质量分布/污染清单/runs 审计），
  引擎 ⚠️选型 N8（duckdb over 本地 parquet 镜像，倾向此案）。
```

---

## 3. 基线实验协议（数字全部实测回填，占位符纪律）

| # | 实验 | 自变量 | 因变量 | 通过标准 |
|---|---|---|---|---|
| E1 | MinHash 参数网格（W4） | num_perm{64,128,256} × bands{8,16,32} × threshold{0.7,0.8,0.9} | 注入重复对的召回率 / 抽样误删率 / 耗时 | 曲线 + 选定参数的误删率 < __%（人工抽检 200 对） |
| E2 | 双引擎对比（W5） | spark / ray / 单机 python（同核数同输入） | 耗时 / 峰值内存 / 结果一致性 | 三方结果 bit-level 一致 + 耗时表 |
| E3 | 门禁阈值扫描（W6） | quality 总分阈值 | 删除率曲线 + LLM 判官一致率(kappa) | 找到「删除率<kappa>」的膝点 |
| E4 | 污染检测（W6） | n{10,13,17} | 评测集命中清单/召回 | 13-gram 基线 + 命中样本人工核验 |
| E5 | 下游闭环（W8） | 四臂（raw/clean-only/dedup-only/full） | 同评测集 Δppl + bootstrap CI | full ≤ raw 的 ppl，且增益可归因 |

**基线的定义**：`v0 = raw 层直接抽样`（不过 clean/dedup/quality），它是所有对比的对照组——在第一次 mix 之前就要生成 v0 快照，否则「治理前」就没有锚点。

---

## 4. 清洗有效性的验证体系（三层递进）

1. **数据内证**（便宜、快）：治理前后同口径统计——重复率（精确+近似）、PII 命中率、语言纯度、长度分布、样板占比。看板的漏斗页就是它的展示面；
2. **下游外证**（唯一核心指标）：§2.9 的 ppl 协议——两个 tiny-LM、一个冻结评测集、bootstrap 置信区间。**这是简历上「困惑度 \_\_→\_\_」的来源，也是变异最大、最需要守协议纪律的一步**；
3. **消融归因**（把「有用」拆成「哪里有用」）：四臂对比，各臂只差一个环节。面试官问「你怎么证明不是安慰剂效应」时，答案在这里。

反例清单（写进 downstream/report.md 的方法论节，防自己翻车）：
- 在不同分布的语料上直接比 ppl（混淆因子）；
- 评测集来自治理后数据（循环论证）；
- 换分词器后跨版本比数字（口径漂移）；
- 不报置信区间只报点估。

---

## 5. 实现顺序与里程碑

依赖序（箭头=阻塞）：`oscar/news/pdf 适配器 → clean 框架 → dedup(python) → [W4 Spark|W5 Ray] → quality → mix → release → downstream`；dashboard/airflow/sql 是旁挂，随时插。

| 批次 | 任务 | 里程碑对齐 |
|---|---|---|
| T6 ⚠️ | oscar + news 适配器（先决：FlClash 实测 OSCAR、news 选型） | W1 全量关账（4 源齐） |
| T7 | pdf 源 + parse | W1 补全解析环节 |
| T8 | clean 框架 + 全部规则 + v0 基线快照 | W2 关账 |
| T9 | dedup 纯 py（exact+minhash+跨源矩阵） | W3 关账 |
| T10 | Spark 版 + E1 网格 | W4 |
| T11 | Ray 版 + E2 | W5 |
| T12 | quality 五维 + 门禁 + 变异测试 + E3/E4 | W6 |
| T13 | mix + release + provenance | W7 前半 |
| T14 | stream + kafka | W7 后半（砍单第 4 位） |
| T15 | downstream 四臂 + 报告 + 收尾 | W8 / M3 |

**绝不砍八项**：清洗、MD5+MinHash 去重、质量门禁、配比、发布、版本/元数据/血缘、幂等断点续跑、下游困惑度验证。
**砍单顺序**（做不动时从上往下砍）：语义去重 → LLM 判官蒸馏 → PDF 源 → Kafka 流式 → 参数网格（留默认组）→ tiny 微调。

---

## 6. 未决事项登记簿（落地前必须关，按紧急度）

| # | 事项 | 选项与倾向 | 关闭时点 |
|---|---|---|---|
| N1 | OSCAR meta/content 实测 | 唯一路径：开 FlClash 直连拉一个 meta 分片 + OSCAR-2201.py | T6 动工前 |
| N2 | 新闻源选型 | 硬条件：免翻墙可下/有稳定主键/体裁纯；候选：THUCNews 直链、HF 镜像上的中文新闻集（待查证后列） | T6 动工前 |
| N3 | PDF 提取引擎分层 | 文本层完好用 PyMuPDF 轻路径，扫描件才 MinerU/OCR（成本悬崖） | T7 |
| N4 | 语言检测器 | fasttext lid.176（快、离线，模型文件经镜像可得）vs 纯 py 库（慢但零依赖）；倾向前者 | T8 |
| N5 | LLM 判官后端 | 便宜 API（有额度）vs 本地 ollama qwen（零成本慢）；倾向 API+本地双后端可切换 | T12 |
| N6 | 蒸馏分类器框架 | fasttext（Windows 轮子风险）vs sklearn char-ngram 逻辑回归（零风险够用）；倾向先 sklearn | T12 |
| N7 | 困惑度后端 | kenlm（CPU 分钟级，经典）vs transformers tiny-LM（贴近真实但慢）；倾向 kenlm 起步、tiny-LM 收尾 | T15 |
| N8 | 看板查询引擎 | duckdb over 本地 parquet 镜像（倾向）vs pandas 全载 | T12 |

---

## 7. 给实现者（quannie）的使用说明

1. 按批次顺序做，每批 = 蓝图对应节 + 任务书细节（找导师要）+ 测试 + commit；
2. 函数签名是契约：实现不得改签名；确需改 → 先改本文档（升版本、记原因），再改代码——这个顺序就是在练「文档驱动开发」；
3. 每个模块完成时在本文档把 ⬜ 改 ✅，commit message 引用批次号（T6…T15）；
4. 蓝图与现实的冲突以现实为准：撞出新事实（如 OSCAR 结构）→ 修订 §对应节 + dev-lessons 记坑，两份文档一起动。
