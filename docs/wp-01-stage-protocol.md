# WP-01 全流程阶段契约（Stage Protocol）v1.0

> 定位：整条管道（ingest→release）的阶段咬合协议。所有后续工作包（clean/dedup/quality/mix/release）
> 的模块契约都必须引用本文。实现语言 Python，宿主为现有 `common/`，新增文件见 §2。
>
> 依赖资产（逐条读过并引用）：
> - `common/idempotent.py`：`run_stage(stage, transform, input_paths, output_dir, force, runs_path, pipeline_version)`（L69）、`fingerprint(paths)`（L25，按路径排序+文件名+长度+sha256）、`_outputs_intact`（L54，缓存验货）、manifest 结构 `{input_fingerprint, output_hashes}`（L84）
> - `common/runs.py`：`start/finish`、finish 拒绝未知 run_id 与重复 finish（L89-105）
> - `common/changelog.py`：`append_change(record, path, dedupe=True)`，dedupe 键 = `(doc_id, stage, action, reason, old_hash)`（L110）；跨字段规则表（L37）
> - `common/first_seen.py`：pending→commit 两段式提交范式（L43-66）
> - `ingest/wiki.py`：`run()` 的既有形状（本契约将其一般化）
> - `schema/unified_schema.json`：spine 必填集、`policies.id/time`、stage_extensions
> - `configs/reason_registry.json`：`stages` 枚举（合法阶段名的唯一来源）
> - `sql/raw_docs.sql`：外部表 + `source=` 分区 + 文件系统分区发现的既有实践
> - `docs/blueprint.md` §0 项目宪法、§1 架构、§2 模块契约

---

## 1. 表路径语法（规范语法，全局强制）

```
本地（调试与指纹）：  data/{zone}/{stage}[/source={s}]/part-*.parquet
MinIO（查询面）：     s3a://{bucket}/{zone}[/source={s}]/part-*.parquet
```

| 项 | 规定 | 违反后果 |
|---|---|---|
| zone 枚举 | `raw/docs`、`clean/docs`、`dedup/docs`、`quality/docs`、`mix/candidates`、`release/v{semver}`、`meta`（台账区，非数据表） | 表无法被 `sql/` DDL 发现 |
| 分区 | `source=` 分区**贯穿 raw→quality**；mix 起取消分区（单批次单目录）；release 按版本目录 | 分区裁剪失效 / 外部表读不到 |
| 分区值来源 | 输出行的 `source` **字段值**（不从输入目录名继承） | 数据与目录不一致 = 查询说谎 |
| part 文件名 | `part-{NNNNN}.parquet`，阶段内全局有序不重复 | fingerprint 顺序不稳定 → 幂等破坏 |
| meta 区 | `data/meta/{runs.jsonl, changelog.jsonl, first_seen/*.json, samples/*.json}` | 审计链断 |

**为什么分区只按 source、不再按日期**：本管道的阶段表按「处理代次」组织而非「到达日期」组织——
代次由 manifest/runs 表达，目录表达的是查询裁剪维度。mix 之后配比维度取代来源维度成为主查询模式。

---

## 2. 新增文件与函数契约（`common/stage.py`，全部由 owner 实现）

### 2.1 配置指纹与快照

```python
def configs_digest(config_paths: list[Path]) -> str:
    """sha256( sorted( relpath + utf8(file_bytes) ) 的串接 )。
    任何一个配置文件缺失 → FileNotFoundError(带路径名)。
    为什么存在：run_stage 的指纹只盖住数据输入；质量阈值/去重参数变了而输入没变时，
    若不重算 = 静默输出过期结果（宪法第 6 条反面）。"""

def snapshot_configs(config_paths: list[Path], stable_dir: Path) -> list[Path]:
    """把配置文件按原名拷进 stable_dir（output_dir/{stage}.configs/），返回拷贝件路径。
    拷贝件供两用：① 并入 run_stage 的 input_paths（参与指纹）② 作为该表产出的配置快照（provenance）。
    写入用先 .tmp 后 replace（同 idempotent._write_manifest 手法）。
    stable_dir 已有同名且内容一致 → 不重写（保持字节稳定，否则快照自身破坏幂等）。"""
```

### 2.2 上游产物定位

```python
class UpstreamNotReadyError(Exception):
    """属性 .stage_dir 指向上游目录。两种触发：manifest.json 不存在；产物验货失败
    （复用 idempotent._outputs_intact 语义）。"""

def latest_outputs(stage_dir: Path) -> list[Path]:
    """读 {stage_dir}/manifest.json，校验 output_hashes 逐文件完好，返回存在的 part 文件路径。
    manifest 缺失/损坏/产物被动过 → raise UpstreamNotReadyError。
    为什么校验而不直接信任：宪法「缓存要验货」的下游延伸——下游有权拒绝读一份被篡改的上游。"""
```

### 2.3 变更日志的两段式提交

```python
class StagedChangeLog:
    """解决「transform 中途崩溃，changelog 已写入半截」的账实分离问题。
    范式与 FirstSeenLedger 同构（pending→commit），这是本项目第二次复用该范式。"""

    def __init__(self, global_path: Path): ...
    def log(self, record: dict) -> None:
        """校验（复用 changelog.validate 语义）后追加写 work_dir 侧的 changelog.part.jsonl。
        校验失败照旧抛 ValueError——坏记录连 part 文件都不许进。"""
    def commit(self, run_id: str) -> int:
        """run 成功后调用：把 part 文件逐条 append_change(dedupe=True) 到 global_path，
        返回新增条数。commit 自身崩溃可安全重试（dedupe 键吸收重复）。"""
    def discard(self) -> None:
        """run 失败路径调用：删除 part 文件。放弃的记录不产生任何全局副作用。"""
```

**账实一致的定义**：全局 changelog 中任何 `run_id` ∈ 成功 run 集合（runs.jsonl 有 finish(success)）。
失败的 run 在 runs 里有尸检记录（finish(failed) + note），在 changelog 里零痕迹——两者的信息分工。

### 2.4 阶段统一入口（每个阶段模块必须暴露）

```python
STAGE: str                      # 必须是 reason_registry.stages 的成员；非法 → import 时即抛

def run(*, output_dir: Path | None = None, force: bool = False,
        runs_path: Path | None = None, pipeline_version: str = ...,
        **stage_kwargs) -> StageResult:
    """阶段唯一入口。内部必须按以下顺序组装（顺序是契约的一部分）：
    1. 定位输入（阶段自定：ingest=download()，其余=latest_outputs(上游)）
    2. snapshot_configs(本阶段配置) → 拷贝件并入 input_paths
    3. run_stage(STAGE, transform, input_paths, output_dir, force, runs_path, pipeline_version)
    4. transform 内：读→变换→写 part 文件；删除/改写走 StagedChangeLog.log()
    5. run_stage 成功返回后 StagedChangeLog.commit(run_id)；失败异常路径 discard()
       （实现提示：run_stage 的 transform 闭包持有 StagedChangeLog 实例即可，无需改 run_stage）"""

StageResult = TypedDict("StageResult", {
    "cached": bool,             # 本次是否零计算
    "run_id": str,
    "output_dir": Path,
    "output_hashes": dict[str, str],   # 文件名 → sha256
    "rows": int | None,         # 实际写入行数；cached 时为 None（零计算的证据）
    "stats": dict,              # 阶段自报：如 clean 的 {rule: {source: removed_n}}；cached 时为 {}
})
```

**manifest 扩展**（对 idempotent.py 现有 manifest 增加字段，向后兼容）：

```json
{ "input_fingerprint": "...", "output_hashes": {...},          // 既有
  "configs_digest": "...", "run_id": "...", "pipeline_version": "...",
  "schema_version": "...", "rows": 230792, "finished_at": "..." } // 新增，均为台账属性
```

**缓存命中条件从二变三**：输入指纹一致 ∧ 产物验货通过 ∧ `configs_digest` 一致。
（实现落点：idempotent 的 manifest 读写与命中判断扩充；本契约只规定行为。）

### 2.5 管道驱动（刻意做薄）

```python
def run_pipeline(stage_calls: list[Callable[[], StageResult]], *,
                 on_fail: Literal["raise", "skip"] = "raise") -> list[StageResult]:
    """按序执行；每个元素是闭包了参数的阶段 run。raise=首个异常即停（默认，Airflow 前
    的生产语义）；skip=记录失败继续（仅用于实验期的消融跑批）。
    不做 DAG、不做并行、不做调度——那是 Airflow 的活。W6 接入 Airflow 时，
    每个 stage_call 1:1 变成一个 BashOperator 任务（`python -m <stage>`），重试交给 Airflow
    （retries=2），幂等性由本协议保证——Airflow 重试与本协议的 cached/force 语义天然咬合。"""
```

### 2.6 CLI 与退出码（`python -m {stage模块}` 统一约定）

| 退出码 | 语义 | 触发 |
|---|---|---|
| 0 | 成功（含 cached） | run 正常返回 |
| 1 | 阶段计算失败 | transform 抛异常（runs 已记 failed，产物目录未动） |
| 2 | 输入数据校验失败 | 行级 schema/主键校验抛 ValueError |
| 3 | 上游未就绪/配置缺失 | UpstreamNotReadyError / FileNotFoundError(configs) |

参数统一：`--output`、`--force`、`--runs-path` 为通用旗标；阶段特有参数各自定义。

---

## 3. content 变换阶段的 spine 语义（全管道最锋利的一条边界）

**规则**：spine 的**身份字段**（doc_id/source/source_id/url/时间戳/license 族/genre）在任何阶段不可变；
**content 族字段**（content/content_hash/char_count/cjk_char_count/utf8_byte_count）在「变更类阶段」
（目前仅 clean 的 redact）**允许整体替换**，条件是：changelog 必须携带 `action=redact` + `old_hash` +
`new_hash` + before/after 字数（schema 已备好全部字段）。下游任何阶段读到的 content 永远是当前版。

**推论**：dedup 的 `normalized_content_hash` 基于**当前版 content** 计算——若某文档在 clean 被 redact，
其去重身份随内容更新（这是期望行为：脱敏后的语料才是发布物，重复判定应对齐发布物）。

---

## 4. 决策记录（三件套 + 证伪方法）

### D1 变换后 content 替换 vs 双列并存（content + content_cleaned）
- **决策**：整体替换 + changelog 留痕（§3）。
- **替代 A**：双列并存——否决：存储翻倍、「content 是唯一正文」不变量被破坏，每个下游都要选列，选错无告警。
- **替代 B**：redact 后视为新文档（新 doc_id）——否决：血缘断裂（doc_id 是 changelog/台账的主键），且「同一文档的治理史」无法串联。
- **证伪**：单测断言 clean 后行满足 `content_hash == ids.content_hash(行内 content)`；若双列或旧哈希残留，此断言变红。审计查询抽 100 条 redact 行，changelog 的 new_hash 必须等于行内 content_hash。

### D2 输入指纹并入 configs_digest（现状缺陷修复）
- **决策**：配置快照拷贝件作为伪输入并入 input_paths（§2.1）。
- **替代 A**：扩展 fingerprint 函数签名吃配置——否决：改公共函数波及全部调用点，且快照件兼任 provenance 的好处丢失。
- **替代 B**：不处理（信任开发者手动 force）——否决：改了质量阈值忘记 force = 静默过期数据，正是宪法第 6 条要杀的事故。
- **证伪**：合同测试 T3（改配置→必须重算）；任何阶段在配置变更后返回 cached=True 即契约失败。

### D3 changelog 两段式提交 vs 直写
- **决策**：StagedChangeLog（part 文件 → 成功后 commit；§2.3）。
- **替代 A**：直写全局（transform 边算边 append）——否决：崩溃 run 的半截记录描述了「从未落地的删除」，看板/归因查询把没删的说成删了（账实分离）。
- **替代 B**：内存缓冲，成功后统一写——否决：进程 OOM 时缓冲全灭，连「哪些行被处理过」的现场都没有；part 文件至少留下尸检材料。
- **证伪**：合同测试 T4（注入 transform 崩溃 → 全局 changelog 零新增 + part 文件残留可查）。

### D4 分区策略：source 贯穿到 quality
- **决策**：`source=` 分区维持到 quality 层，mix 起取消（§1）。
- **替代 A**：全链不分区——否决：W3 跨源矩阵、W6 异常归因都要按源切片，全表扫描在 10⁷ 行级不可接受。
- **替代 B**：每阶段重分区（如 dedup 按 dup_cluster）——否决：重分区 = 全量洗牌，违背「阶段只追加不重排」的朴素性，且 cluster 分区在 dedup 前不存在。
- **证伪**：EXPLAIN 抽查 quality 层 `WHERE source='oscar'` 必须出现 PartitionFilters；若出现全表扫即分区失效。

### D5 驱动器做薄（run_pipeline）vs 直接只在 Airflow 编排
- **决策**：先薄函数（§2.5），Airflow W6 才进场。
- **替代 A**：跳过驱动器，W6 直接 Airflow——否决：在 T8-T15 期间没有统一入口，阶段串联靠手敲命令，回归测试无锚点。
- **替代 B**：上 prefect/dagster——否决：又一个 W4 级基础设施坑，收益为零（DAG 拓扑是线性的）。
- **证伪**：若 T8-T15 期间出现「需要条件分支/并行扇出」的真实需求（目前拓扑是线性链+四源扇入），薄驱动不够用时再升级，升级点记录为新决策。

---

## 5. 合同测试（`tests/stage_contract.py`，参数化跑所有阶段）

每个阶段模块必须暴露钩子：

```python
def contract_fixture(tmp_path: Path) -> Callable[..., StageResult]:
    """返回一个预置了微型输入的 run 可调用（闭包 output_dir=tmp_path/...）。"""
```

| # | 测试 | 断言 |
|---|---|---|
| T1 | 幂等 | 二跑 cached=True、rows=None、产物哈希不变；runs 出现 executed→cached 序列 |
| T2 | 强制重算字节一致 | force=True 后 output_hashes 与首跑逐字节相等 |
| T3 | 配置指纹 | 修改任一本阶段配置文件后重跑 → cached=False（必须重算） |
| T4 | 崩溃账实分离 | transform 注入异常 → 产物目录不变、runs 记 failed、全局 changelog 零新增、part 文件存在 |
| T5 | 上游验货 | 手改上游 part 文件一字节 → latest_outputs 抛 UpstreamNotReadyError |
| T6 | StageResult 形状 | 六键齐全、类型正确、cached 时 stats=={} |

wiki 模块（已存在）按本契约补 `contract_fixture` 后成为第一个受测者；后续每个阶段合入即自动进套件。

## 6. 验收标准（WP-01 自身的完成定义）

1. 本文档 §1-§5 全部小节有实质内容，无 TBD；✅
2. 每个函数有签名+类型+错误语义；✅
3. 五个决策各有 ≥2 替代方案对比与证伪方法；✅
4. 合同测试六条可直接翻译成 pytest（输入输出明确）；✅
5. 与既有代码的兼容边界说清（manifest 增字段向后兼容、wiki.run 形状一般化）；✅
6. Airflow 映射关系说清但不在本轮实现；✅（这是契约文档，实现属于 WP-09）
7. owner 拿着 §2 能直接写 `common/stage.py` 与改造各阶段入口。⚠️ 唯一保留：`StagedChangeLog.commit`
   与 run_stage 成功路径的衔接点（§2.4 第 5 步）给了实现提示而非代码——按"契约不写实现"的边界，这是刻意留白。
