# T5 交付说明：`ingest/wiki.py`（wiki 适配器 + W1 终极验收）

> 一次交付：代码 + 测试 + `pytest` 绿灯 + 验收 SQL 输出 + registry 修订。分片数用 1。
> 四道口试题（#004/#005 的 owner 补答）按 owner 要求口头交卷，不入仓。

## 一、交付清单

| 文件 | 作用 |
|---|---|
| `ingest/wiki.py` | 适配器本体：`download` / `iter_docs` / `run` 三个函数，映射 + 两级幂等 |
| `ingest/__init__.py`、`ingest/README.md` | 包说明 + 适配器契约（新增源只加模块 + registry 一行） |
| `common/textstats.py` | `char_count` / `cjk_char_count` / `utf8_byte_count`（纯函数，T6 复用同一口径） |
| `common/first_seen.py` | `first_seen_at` 台账（重跑沿用旧值，见第六节） |
| `tests/test_ingest_wiki.py` | 11 条：doc_id 确定性、golden record、schema 键封闭、registry 唯一来源、流式、缺列/缺主键、幂等、force 字节一致、台账、分片数校验 |
| `tests/test_textstats.py`、`tests/test_first_seen.py` | 5 + 5 条 |
| `sql/raw_docs.sql` | 外部表 `raw_docs` + 验收 SQL + 分区裁剪证据（可重复执行） |
| `configs/source_registry.json` | v0.3.0：wiki 的 `source_id_policy` / `content_format` / `language` / `metadata_schema` |
| `schema/unified_schema.json` | v0.3.2：`policies.content` / `policies.source_id` 与事实对齐 |
| `docs/source_metadata_key_registry.md` | `wiki.revision_id`、`wiki.namespace` 标 deprecated |
| `requirements.txt` | `pyarrow==23.0.1`；`tests/test_registry.py` 的 REQUIRED_PACKAGES 同步 |

`pytest -q` → **68 passed**（原 47 + 新增 21）。

## 二、实测数字（分片 0，不是估算）

| 指标 | 值 |
|---|---|
| 源分片 | `20231101.zh/train-00000-of-00006.parquet`，**587,109,027 B** |
| 源行数 | **230,792**（任务书估"约 30 万"，实际 23 万） |
| 产物 | `data/raw/wiki/ingest/part-00000.parquet`，**511,179,953 B**（zstd） |
| 总字符数 | 345,270,977（平均 1,496 字符/篇，最短 6，最长 185,556） |
| 主键 | `doc_id` 与 `source_id` 各自 230,792 个唯一值，零重复、零 null |
| 耗时 | ingest 约 2 分钟（下载已在 HF 缓存；下载级幂等白拿） |
| 第二遍 | `cached=true`、`rows=null`（transform 零执行）、产物 sha256 不变 |
| 上传 | 487.50 MiB → `s3a://raw/docs/source=wiki/part-00000.parquet`，`size=511179953` 与本地一致 |

## 三、字段映射（实际取值）

| 源列 | spine 字段 | 实际做法 |
|---|---|---|
| `id`（页面 ID） | `source_id` | `str(id)`；`doc_id = spine_doc_id("wiki", id)` |
| `text` | `content` | 原样，不改一个字符（"只映射不清洗"） |
| `text` | `content_hash` | `common.ids.content_hash(content)`，ingest 即算 |
| `url` | `url` | 原样（percent-encoded；归一留给 clean） |
| `title` | `source_metadata.page_title` | 唯一登记的 key |
| — | `license` / `license_status` / `commercial_use` | `source_registry`（CC BY-SA 4.0 / permissive / allowed） |
| — | `genre` | `source_registry.default_genre` = `wiki` |
| — | `content_format` | `source_registry.content_format` = `plain_text`（见第四节） |
| — | `language` | `source_registry.language` = `zh`（**源声明，不是检测**，故不写 `language_confidence`） |
| — | `char_count` / `cjk_char_count` / `utf8_byte_count` | `common.textstats`（码点 / 汉字 / UTF-8 字节） |
| dump 日期 | `source_timestamp` | `2023-11-01`，`timestamp_source="meta"`（date 粒度，schema 允许） |
| — | `first_seen_at` | 台账（第六节） |
| — | `schema_version` | 读 `schema/unified_schema.json` 的 `version`，不在代码里写死 |

### "未检测"不是"空"——三条约定

| 状态 | 写法 | 本适配器的例子 |
|---|---|---|
| **未检测**（raw 阶段没有这个信息） | 字段**不存在** | `language_mix`、`dominant_script`、`language_confidence` |
| **确认为无**（schema 允许 null 且能断言源里没有） | 显式 `null` | `canonical_url`（URL 归一属 clean）、`raw_uri`（wiki 没有留原始 dump 文件）、`original_encoding`（Arrow 已是 UTF-8，原始字节编码不可知，不冒充） |
| **有值** | 写值 | `language="zh"`（说清来源是声明，才不写置信度） |

写 `language_mix = {}` 或 `language_confidence = 0.0` 是在撒谎——那等于说"检测过了，结果是空/零"。

## 四、肉眼看 10 条：`content_format` 的结论

看了 12 条样例（JSON 原始行，不是渲染后的）+ 6,000 条抽样统计：

| 检查 | 命中率 | 说明 |
|---|---|---|
| `[[`（wiki 内链） | 0.25% | 残余，不是主体 |
| `{{`（模板） | 0.72% | 同上 |
| `<ref>` | 0.45% | 同上 |
| `'''`（粗体） | 0.37% | 同上 |
| HTML 标签 | 1.67% | 主要是残留 `<br>` 之类 |
| 行首 `==标题==` | 0.02% | 章节标题已被压成单行文本 |
| `{|`（表格） | 0.67% | |

**结论：`text` 是 WikiExtractor 式清洗后的纯文本（plain text），不是 markdown，也不是 wiki markup。**
段落用空行分隔、章节标题退化成一行短文本、模板与外文名被剥掉后留下空括号 `（）`
（例：`文學（），在狭义上…`）——这些残留是 **clean 阶段的活**，ingest 一个字都不动。

另外一条事实：zh dump **不做繁简转换**，同一篇文章里简繁混排是常态
（`数学，是研究數量…`）。`language` 只能是 `zh`，要不要再细分 `zh-Hans/zh-Hant`
是 clean 的语言识别 + 繁简折叠策略问题（对应 `configs/text_normalization.json` 的
`traditional_to_simplified_mode=t2s`）。

## 五、registry / schema 修订（registry 必须和事实一致）

| 位置 | 旧 | 新 | 依据 |
|---|---|---|---|
| `wiki.source_id_policy` | `revision_id（稳定主键）` | `page_id（…HF 镜像不含 revision_id）` | 镜像只有 `id/url/title/text` 四列 |
| `wiki.content_format` | `markdown` | `plain_text` | 第四节实测 |
| `wiki.language` | （不存在） | `zh` + `language_source: declared` | 源声明；适配器不许自己写语言 |
| `wiki.metadata_schema` | `page_title`/`revision_id`/`namespace` | 只剩 `page_title` | 镜像没有后两列，登记表同步标 deprecated |
| `source_registry.version` | 0.2.0 | 0.3.0 | 本次修订 |
| `unified_schema.policies.content` | "content 存规范化 Markdown" | "格式由 registry 的 `content_format` 声明" | 原文与新事实冲突；wiki 是 plain_text |
| `unified_schema.policies.source_id` | `wiki revision_id` | `wiki 页面 ID` | 同上 |
| `unified_schema.version` | 0.3.1 | 0.3.2 | policy 文本变更 |

## 六、两级幂等 + `first_seen_at` 台账

```
hf_hub_download（下载级：缓存 + 断点续传，免费）
      ↓ 输入 = 已下载的分片文件
run_stage("ingest", ...)（处理级：输入指纹不变 → 跳过，T4 造的）
```

T4 的 `fingerprint` 输入是"分片文件列表"，所以下载级和处理级天然咬合：
分片没变 → 整个阶段 `cached=True`，transform 零执行，台账零写入。

**为什么多一个台账**：schema 的 `policy.time` 说 `first_seen_at` 要"重跑沿用旧值，参与幂等校验"。
若每次写 `now()`，T4 承诺的"同样输入两次跑出的文件字节一致"当场失效（字节里躺着每次都在变的时钟）。
所以 `data/meta/first_seen/wiki.json`（21.9 MB / 230,792 条）记 `doc_id → 首次入库时间`，
transform 成功返回后才 `commit`（先写 `.tmp` 再 replace）。这也顺手证了另一件事：
**`force` 重跑能得到一模一样的字节**（`test_forced_rerun_is_byte_identical`）。

## 七、为什么数据行里没有 `ingest_run_id`

`stage_extensions.ingest` 里有 `ingest_run_id`，但写进 parquet 行 = 行内容随运行而变 = 字节级确定性报废。
行的归因走 `data/meta/runs.jsonl`（start/finish 对）+ manifest（输入指纹 / 产物 sha256）。
**数据行只承载事实，运行上下文不混进数据**；真要做行级归因，应该扩展 manifest，不是污染 spine。

## 八、W1 终极验收（可重复执行）

```bash
# 1) 建桶（检查单 #4 的遗留：MINIO_DEFAULT_BUCKETS 只自动建 warehouse/logs）
docker compose exec -T minio mc mb --ignore-existing local/raw

# 2) 两段式上传：本地文件先可检查，再 mc cp
docker compose cp data/raw/wiki/ingest/part-00000.parquet minio:/tmp/part-00000-wiki.parquet
docker compose exec -T minio mc cp /tmp/part-00000-wiki.parquet local/raw/docs/source=wiki/part-00000.parquet

# 3) 建外部表 + 验收
docker compose cp sql/raw_docs.sql spark-master:/tmp/raw_docs.sql
docker compose exec -T spark-master /opt/spark/bin/spark-sql \
    --conf spark.sql.hive.manageFilesourcePartitions=false -f /tmp/raw_docs.sql
```

输出：

```
wiki	230792
Time taken: 2.029 seconds, Fetched 1 row(s)

== Physical Plan ==
+- HashAggregate(keys=[], functions=[count(1)])
   +- Exchange SinglePartition, ENSURE_REQUIREMENTS, [plan_id=69]
      +- HashAggregate(keys=[], functions=[partial_count(1)])
         +- Project
            +- FileScan parquet spark_catalog.default.raw_docs[source#24] Batched: true, DataFilters: [],
               Format: Parquet, Location: InMemoryFileIndex(1 paths)[s3a://raw/docs],
               PartitionFilters: [isnotnull(source#24), (source#24 = wiki)], PushedFilters: [],
               ReadSchema: struct<>
```

`PartitionFilters: (source#24 = wiki)` 就是"分区裁剪"的证据——`WHERE source='wiki'`
在文件列举阶段就把别的分区目录裁掉了，没有一行转换代码。

### 验收时撞出来的三个坑（都已进 `sql/raw_docs.sql` 注释）

1. **Hive 拒绝"桶根"作为表位置**：`LOCATION 's3a://raw/'` 直接
   `InvalidObjectException: raw_docs location must not be root path`。
   所以数据放**多一级前缀**：`s3a://raw/docs/source=wiki/...` + `LOCATION 's3a://raw/docs/'`。
   `source=...` 仍是目录名，分区裁剪不受影响；多这一级换的是"Hive/Trino 将来也能读"。
2. **metastore 没有 S3A 凭据**：`got exception: AccessDeniedException s3a://raw/docs:
   NoAuthWithAWSException`。任何需要 metastore 去碰 s3a 的动作都会挂——注册分区（`MSCK REPAIR`）、
   建库（`CREATE DATABASE` 要 mkdir）、校验已存在的表位置。表现是 Spark 降级把表存成
   "Spark SQL specific format"（Spark 自己能查，别的引擎读不懂 schema）。
   修法在 infra 侧：给 metastore 容器注入 `AWS_ACCESS_KEY_ID/SECRET` + `fs.s3a.*` 属性
   （还得确认镜像里有 `hadoop-aws` jar）→ 已进 backlog，不在 T5 范围。
3. **分区交给文件系统发现**：`--conf spark.sql.hive.manageFilesourcePartitions=false`。
   对 append-only 的 raw 层这本来就更对：`mc cp` 上传完就能查，不需要 `MSCK REPAIR`；
   curated 层（W7 Iceberg）另有一套元数据。

## 九、已知限制 / 下一棒

- **只跑了分片 0**（23 万行 / 345M 字符）；`n_shards` 参数已支持 1..6，`_write_shard` 按分片产出 `part-0000N.parquet`，没跑满 6 个是刻意的（W1 验收不需要）。
- **`source_metadata` 只有 `page_title`**：HF 镜像没有 revision/namespace，登记表已标 deprecated；哪天换回 dump + revision，是 registry 加 key 的事，不改适配器结构。
- **`canonical_url` 全是 null**：URL 归一的 config（`configs/url_canonicalization.json`）已有，但那是 clean/统一阶段的活；ingest 只搬原值。
- **台账体积**：23 万条 / 21.9 MB JSON，全量 6 分片 ≈ 140 万条时该换 SQLite 或按 source 分桶——已进 backlog。
- **上传仍是手工两条命令**：等 DAG（Airflow）接上去之后应该是一个 task，不是人肉 `mc cp`。
- **慢变量**：单分片 2 分钟、内存稳定（batch 10k 行），但 `fingerprint` 会把 587MB 分片整个读一遍算 sha256——分片变大后这里会变成瓶颈，属于 T4 的已知代价。
