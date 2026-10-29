# datalens 架构说明（ARCHITECTURE）

> 本文是 **as-built** 文档：只描述当前已存在的代码与行为。规划中的模块（M2+）只出现在
> §6 路线图并指向 `docs/blueprint.md`。完整设计决策在 `docs/wp-01-stage-protocol.md`（契约）
> 与 `DEVELOPMENT_LOG.md`（决策日志，编号 M{n}-D{k} 被代码注释引用）。

## 1. 数据流（当前实态）

```
wikimedia/wikipedia (HF, 20231101.zh)
        │  hf_hub_download（下载级幂等：缓存/断点/校验）
        ▼
ingest/wiki.py  iter_docs ──► spine 行流（doc_id/content_hash/registry 字段）
        │  run_stage("ingest", ...)（处理级幂等：指纹+验货+审计）
        ▼
data/raw/wiki/ingest/part-00000.parquet ──mc cp──► MinIO s3a://raw/docs/source=wiki/
        │
        ▼
Spark 外部表 raw_docs（USING parquet, PARTITIONED BY source, 文件系统分区发现）
        ▼
SELECT source, COUNT(*) FROM raw_docs GROUP BY source;   ← W1 验收（wiki=230792 ✅）

横切（被所有阶段消费）：
  data/meta/runs.jsonl        run 生命周期事件（start/finish, status, note）
  data/meta/first_seen/*.json doc_id → 首见时间台账（pending→commit）
  data/meta/changelog.jsonl   变更日志（M4 起有数据；schema 已就位）
```

## 2. 容器栈（docker-compose，7 服务）

| 服务 | 镜像 | 状态/角色 | 关键定制 |
|---|---|---|---|
| postgres | postgres:16-alpine | healthy；metastore+airflow 两个逻辑库 | init.sql 建 airflow 库 |
| minio | bitnamilegacy/minio:2025.5.24 | healthy；数据本体 | MINIO_DEFAULT_BUCKETS 自动建 warehouse/logs；数据目录 /bitnami/data/minio |
| metastore | apache/hive:4.0.0 | Up；表目录（thrift:9083） | SERVICE_OPTS 传 jdo + s3a；外挂 PG 驱动 + hadoop-aws 3.3.6 |
| spark-master/worker | corpus/spark:3.5.3-s3a（自建） | Up；计算 | Dockerfile 补 hadoop-aws 3.3.4 + sdk-bundle 并 chmod；conf 无密钥（env 注入凭据） |
| airflow | apache/airflow:2.10.4 | Up；调度（DAG 未挂，M9） | LocalExecutor；DAG/日志挂卷 |
| kafka | bitnamilegacy/kafka:3.7.1 | Up（未验收，M9） | KRaft 单节点无 ZooKeeper |

网络：compose 默认网络 `corpus-net`，服务名即 DNS。发布端口仅 5 个（9000/9001/7077/8080/8081）。

## 3. 模块清单（as-built）

| 模块 | 输入 | 输出 | 关键函数 |
|---|---|---|---|
| `common/ids.py` | source+source_id / 文本 / 文本+config | 确定性 ID 与哈希 | `spine_doc_id` `content_hash` `normalized_content_hash` |
| `common/runs.py` | stage 名 | run_id / 事件流 | `start` `finish` `read_events` `query_by_run` |
| `common/changelog.py` | 变更记录 dict | 校验后的 JSONL 追加 | `validate` `append_change(dedupe)` `query_by_doc` `count_by_reason` |
| `common/idempotent.py` | transform+输入 | 产物目录+审计 | `run_stage` `fingerprint`（缓存命中 = 指纹∧目录∧逐文件验货） |
| `common/first_seen.py` | doc_id | 首见时间 | `FirstSeenLedger.resolve/commit` |
| `common/textstats.py` | str | 三计数 | `text_stats` `cjk_char_count` |
| `common/log.py` | LOG_LEVEL | stderr 日志 | `setup` `get_logger` |
| `ingest/wiki.py` | HF 分片 parquet | spine parquet | `download` `iter_docs` `map_row` `run` |
| `ingest/oscar.py` | mock jsonl（N1 后换真实源） | spine parquet + quarantine parquet | `write_mock_shards` `classify_row` `run`（护栏 0.05） |
| `ingest/sampling.py` | 分片清单 | 确定性抽样 + manifest 快照 | `sample_shards` `write_sample_manifest` |
| `clean/pii.py` | 文本 | 命中/脱敏 | `detect` `redact`（GB11643 + Luhn 校验） |
| `sql/raw_docs.sql` | — | 外部表 DDL + 验收查询 | — |
| `smoke/m1.py` | — | 五步 PASS/FAIL | — |

## 4. 阶段间契约（当前实态 + WP-01 目标态）

**已实装**：ingest 的 run() 形状 = download → run_stage(指纹/验货/审计) → 本地 parquet →
人工 mc cp 上 MinIO。数据行 = spine 字段子集（未检测字段不出现，见 wiki.py 模块头三分法）。

**契约目标态**（WP-01，M4 起实装，此处只列差异）：表路径语法（`data/{zone}/{stage}/source={s}/`）、
`configs_digest` 并入指纹、`StagedChangeLog` 两段式提交、`latest_outputs` 上游验货、
CLI 退出码 0/1/2/3。完整契约与五个决策记录（D1-D5）见 `docs/wp-01-stage-protocol.md`。

## 5. 关键决策索引（详见 DEVELOPMENT_LOG M1 与 wp-01）

| 决策 | 一句话 | 证伪方法 |
|---|---|---|
| M1-D1 时钟不进数据行 | first_seen 走台账，force 重算字节一致 | `test_forced_rerun_is_byte_identical` |
| M1-D2 缓存要验货 | 命中三条件含逐文件哈希对账 | 篡改产物测试 |
| M1-D3 run 即审计 | 失败也留 finish(failed) 痕迹 | 集成测试事件序列断言 |
| M1-D4 未检测≠空 | 字段三态：不出现/null/有值 | golden record |
| M1-D5 事实单一来源 | registry 字面量禁入代码 | 源码 grep 元测试 |
| M1-D6 日志双通道 | stderr 日志 / stdout 结构化 | — |
| wp-01 D1-D5 | content 替换语义/配置指纹/changelog 两段式/分区策略/薄驱动 | 各附测试，见 wp-01 §4 |

## 6. 路线图位（不展开，指向蓝图）

M2 OSCAR（阻塞中：FlClash + N1 实测）→ M3 news/pdf → M4 clean（WP-01 实装）→
M5 dedup → M6 quality → M7 mix/release → M8 downstream ppl → M9 stream/DAG → M10 dashboard。
模块级契约与实验协议：`docs/blueprint.md` §2/§3/§4；工作包队列：wp-01 文尾。
