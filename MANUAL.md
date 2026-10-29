# datalens 操作手册（MANUAL）

> 你想改什么 → 查反查表 → 去那个文件那个参数改。改完「要重跑哪些阶段」列告诉你影响面。
> 通用规则：**改了任何进入数据或配置的东西，下游阶段必须 force 重算**（M4 前指纹不含配置，
> 属已知缺陷，见 DEVELOPMENT_LOG M1 遗留）。

## 反查表：我想改 X → 去哪改

| 我想改… | 位置 | 当前值 | 影响范围 | 改后要重跑 |
|---|---|---|---|---|
| **数据源（加一个源）** | `configs/source_registry.json`（登记 license/genre/format/id 策略）+ 新建 `ingest/<source>.py` 适配器（照 `ingest/wiki.py` 形状）+ `docs/source_metadata_key_registry.md`（登记 map 键） | 4 源已登记（wiki/oscar/news/pdf），适配器实装 1 个 | 新增源；已有源不受影响 | 新适配器自己的 ingest；下游全链路（新源汇入） |
| 换 wiki 的 dump 日期/分片数 | `ingest/wiki.py` 顶部 `DUMP_DATE` / `N_SHARDS` / `SHARD_FILENAME` | 20231101 / 6 | doc 的 source_timestamp 与数据量 | `python -m ingest.wiki --force 不存在时删 data/raw/wiki/` → 重新上传 MinIO → S5 |
| **清洗规则**（增删改） | M4 落地：`configs/`（规则阈值注册）+ `clean/rules/` 纯函数；reason 码登记 `configs/reason_registry.json` | 尚未实装（clean 框架属 M4） | clean 层及全部下游 | M4 起生效；届时本表更新 |
| **去重阈值** | M5 落地：`configs/dedup_policy.json`（num_perm/bands/threshold/shingle） | 尚未实装（M5） | dedup 层及下游 | M5 起生效 |
| **质量门禁阈值** | M6 落地：`configs/quality_weights.json` + 门禁阈值 | 尚未实装（M6） | quality/mix/release | M6 起生效 |
| **抽样比例/配比** | M7 落地：`configs/mix_policy.json`（strata/ratios/token_budget/seed） | 尚未实装（M7） | mix 输出 | M7 起生效 |
| **输出路径（本地产物）** | `ingest/wiki.py` 的 `OUTPUT_DIR`（L91） | `data/raw/wiki/` | 该阶段产物位置；MinIO 上传目标同步改 | 仅影响新跑；已有产物不迁移 |
| **输出路径（MinIO/表）** | 上传命令里的目标前缀 + `sql/raw_docs.sql` 的 `LOCATION` | `s3a://raw/docs/` | 外部表位置 | 重跑 S5（DROP/CREATE 自动） |
| **日志级别** | 环境变量 `LOG_LEVEL`（`common/log.py` 消费） | `INFO` | 全部入口的 stderr 日志量 | 无需重跑，即时生效：`LOG_LEVEL=DEBUG python -m ingest.wiki` |
| 文本规范化规则 | `configs/text_normalization.json`（改任意规则**必须**递增 `version`） | v1.0.0（NFKC+t2s 折叠等，见 decisions 节） | normalized_content_hash 口径 = 去重判定 | M5 起生效；改规则 → dedup 及下游 force |
| wiki 的 license/genre 等声明 | `configs/source_registry.json` 的 `sources.wiki` | CC BY-SA 4.0 / wiki | 新 ingest 的行；旧行不追溯 | force 重跑 ingest（golden record 测试会同步要求更新） |
| 单条记录的 schema 字段 | `schema/unified_schema.json`（spine/stage_extensions）+ `ingest/wiki.py` 的 `PARQUET_SCHEMA` + golden 测试 `tests/test_ingest_wiki.py` | v0.3.2 | 全管道契约 | schema_version 递增 + 全链路重算 |
| 容器栈（服务/端口/凭据） | `docker-compose.yml`；凭据 `.env`（不进 git） | 7 服务 | 基础设施层 | `docker compose up -d` 重建受影响服务 |
| Metastore 的 S3A/DB 连接 | `docker-compose.yml` metastore 服务的 `SERVICE_OPTS` | 见文件内注释 | metastore | `docker compose up -d metastore --force-recreate` |

## 微调点索引（总表）

> 「后续里程碑将新增」不是 TBD——是本手册随里程碑增行的既定节奏（每个里程碑交付新增行）。

| 微调点 | 位置 | 当前值 | 影响范围 | 调整建议 | 关联阶段 |
|---|---|---|---|---|---|
| wiki 分片数 | `python -m ingest.wiki N` 或 `ingest/wiki.py run(n_shards=)` | 1 | 数据量 | 验收用 1；W3 去重实验扩到 6 | ingest |
| 抽样种子/份数 | M2 起 `ingest/sampling.py`（WP-01 契约已定） | 未实装 | OSCAR 首跑样本 | 确定性抽样，种子进 git | ingest |
| 规范化 version | `configs/text_normalization.json` 的 `version` | 1.0.0 | 哈希口径兼容性 | 只递增不回退；改动写 decisions 理由 | clean/dedup |
| PII 掩码策略 | `clean/pii.py` 的 `MASK_SPANS` | id(3,4)/bank(4,4)/phone(3,4) | 脱敏产出 | 头尾保留位数权衡泄漏 vs 可读性 | clean |
| 批处理大小 | `ingest/wiki.py` 的 `BATCH_SIZE` | 10000 | 内存占用 | OOM 才降；不影响产物字节 | 全阶段 |
| pytest 范围 | `pytest.ini` | 全 tests/ | 验证面 | — | 测试 |
| 日志级别 | `LOG_LEVEL` 环境变量 | INFO | 日志量 | DEBUG 排障，INFO 日常 | 全阶段 |
| 强制重算 | 各阶段 `run(force=True)`；CLI `--force` | False | 缓存旁路 | 改配置后必须（M4 前） | 全阶段 |
| runs/changelog 台账路径 | `common/runs.py`/`changelog.py` 顶部常量 | `data/meta/` | 审计账本位置 | 不要动，看板依赖 | 全阶段 |
| 清洗规则阈值 | M4 起 `configs/`（clean） | 未实装 | clean 删除率 | 阈值扫描实验 E3 定 | clean |
| 去重参数 | M5 起 `configs/dedup_policy.json` | 未实装 | 误删/漏重曲线 | 网格实验 E1 定 | dedup |
| 质量权重/门禁 | M6 起 `configs/quality_weights.json` | 未实装 | 门禁通过率 | kappa 审计校准 | quality |
| 配比与 token 预算 | M7 起 `configs/mix_policy.json` | 未实装 | 发布集构成 | 按下游 ppl 反馈调 | mix |

## 常见操作配方

**改了配置/代码后完整重跑**（M4 前：“指纹不含配置”缺陷下的标准姿势）：

```bash
python - <<'PY'
from ingest import wiki
r = wiki.run(force=True)
print(r["cached"], r["rows"])
PY
# 然后重新上传 MinIO + python smoke/m1.py
```

**看某条文档的完整治理史**（M4 起 changangelog 有数据后）：

```bash
python - <<'PY'
from common.changelog import query_by_doc
for r in query_by_doc("<doc_id>"): print(r)
PY
```

**看跑了哪些 run**：`data/meta/runs.jsonl` 直接读，或
`python -c "from common import runs; [print(e) for e in runs.read_events()]"`
