# 开发日志（DEVELOPMENT_LOG）

> 模板：日期 / 里程碑 / 目标 / 实际做了什么 / 关键决策及理由 / 遇到的问题及解决 /
> 怎么验证的 / 微调入口 / 遗留问题。决策编号 M{里程碑}-D{n} 会被代码内联注释引用。

---

## M2 — OSCAR 适配器（mock 骨架，真实数据待 N1）（2026-10-30）

**日期**：2026-10-30。**里程碑**：M2。
**目标**：OSCAR 源接入骨架全通——确定性抽样、quarantine 一等公民、坏行护栏、双层幂等；
真实取数被 N1 阻塞（FlClash 未开，OSCAR meta/content 结构未实测），按里程碑规则 mock 先行。

### 实际做了什么

- `ingest/spine_schema.py`：spine 产物 schema 从 wiki 私有常量升级为家族契约（wiki.py 改为
  import 别名，测试零改动）；
- `ingest/sampling.py`：`sample_shards`（sha256(seed:shard) 排序取前 k，与输入顺序无关）+
  `write_sample_manifest`（抽样决策落盘为字节稳定快照，进 git = 可复现）；
- `ingest/oscar.py`：`write_mock_shards`（确定性 mock 源，坏行为 3 类配方）→ `classify_row`
  （N1 后唯一需要重写的接缝：一行 → ok-spine | quarantine 记录）→ `_write_shard`（护栏判定 +
  双产物 part/quarantine parquet）→ `run()`（run_stage 幂等，quarantine 产物也进 output_hashes
  参与验货）；策略阈值全部读 `configs/ingest_policy.json`；
- `tests/test_ingest_oscar.py`：12 个测试。

### 关键决策及理由

- **M2-D1 spine schema 家族化**：第二个适配器出现，产物 schema 收敛到共享文件；
  改它 = 改全家族产物 → 必须 schema_version 递增 + 全链 force。
- **M2-D2 抽样 = 快照 = 指纹**：确定性整群抽样 + manifest 落盘，manifest 并入 run_stage
  输入指纹，幂等白拿；整群偏差记为 W4 实验局限（blueprint E1 注）。
- **M2-D3 身份策略（N1 前的假设）**：warp_record_id 为 source_id（registry 声明），
  同页两次爬取 = 两条 raw 记录（爬取事实保留），内容折叠交给 M5 dedup；缺身份 → quarantine
  而非发明 ID。
- **M2-D4 坏行双策略（宪法第 7 条的显式豁免）**：wiki 干净源缺主键 fail-fast；oscar 脏源
  行级异常 quarantine + 5% 分片护栏（区分毒丸行与源结构漂移）。策略差异入 config 不入代码。

### 遇到的问题及解决

- 首版 `classify_row` 把「非字符串 content」和「空 content」合并成一个条件，测试
  `row2-bad_content_type` 当场逮住（reason 误报 empty_content）——拆成两个分支修复。
- **护栏语义重构（实跑教训，M2-D5）**：首版护栏是「每片独立判 5%」。CLI 默认路径实跑
  必炸——mock 分片只有 50 行，二项方差大到真实坏行率 3% 时单片超 5% 的概率约 55%；
  即使把 mock 默认降到 3%，4 片里仍有约 1/4 概率撞线。根因是把 run 级属性（源结构
  漂移）放到了片级判定。重构为 `_check_guardrail`：**run 级聚合判定**，消息携带分片
  明细；单片的随机波动被聚合稀释（3/200=1.5% 不触发），系统性漂移（全坏）必触发。
  重构过程中还修了一个测试构造错误：4 片各 3/50 是系统性 6% 而非「波动」，护栏拒它
  是正确行为——错的是测试数据，不是代码。
- 审计链同步验证：护栏触发的那次运行在 runs.jsonl 里留下 `status: failed` +
  note=完整异常——「失败留痕」按设计工作。

### 怎么验证的（全部实跑）

```
$ python -m pytest -q
80 passed in 2.64s          （68 → 80）

$ python -m ingest.oscar    # 修复后首次实跑（mock 4×50, 聚合坏行 2.5%）
{"cached": false, "rows": 195, "quarantined": 5, "run_id": "ingest-20261029T183606Z-112d75b5", ...}

$ python -m ingest.oscar    # 重跑
{"cached": true, "rows": null, "quarantined": null, ...}   ← 零计算的证据

$ tail -2 data/meta/runs.jsonl   （护栏事件留痕）
{"event": "finish", "status": "failed",
 "note": "ValueError: oscar_part_4.jsonl 坏行率 6.0% 超护栏 5%（3/50）……", ...}
```

### 微调入口

MANUAL 反查表新增：OSCAR 护栏阈值/语言默认（configs/ingest_policy.json）、抽样 seed/k
（同文件 sample 节）、mock 源目录（ingest/oscar.py MOCK_DIR）。

### 遗留问题

1. **N1 未关**（FlClash）：真实 OSCAR meta 行结构、正文获取路径、language 字段可信度
   三件事实测后重写 download() 与校准 classify_row；
2. run() 的 mock_dir 注入用了全局变量临时交换——能用但丑，M4 stage 化时随契约重构；
3. oscar 上传 MinIO（source=oscar 分区）待真实数据到位后进 smoke。

**日期**：2026-10-26 起，10-30 收口（smoke PASS）。
**里程碑**：M1（对应蓝图 §5 的 W1 段：环境、契约、wiki 源、验收）。
**目标**：空目录 → 统一 schema 契约 → 可靠性底座 → 容器栈 boot → wiki 源落 raw 层 →
一条 SQL 查到行数（W1 验收原句），且全程幂等可审计。

### 实际做了什么（文件 → 函数粒度）

1. **契约层**（`schema/`、`configs/`）：
   - `schema/unified_schema.json` v0.3.2：spine 23 字段 + 四个 stage_extensions；
     policies 节钉死 `doc_id = sha256(source:source_id)`（键材料永不扩展）与
     content/canonical_url 分工（v0.3.1 修：canonical_url 移出键材料防版本漂移）；
   - `schema/removal_log_schema.json`：action 四态（remove/modify/redact/quarantine）+
     run_id + pii_types + old/new_hash；字段改名 changed_by/changed_at（语义泛化跟随）；
   - `configs/source_registry.json`：4 源登记（license/status/genre/format/language/
     source_id_policy 逐源声明）；`configs/reason_registry.json`：12 个 reason 码与
     action/stage 的映射（W6 门禁的配置载体）；`configs/text_normalization.json` v1.0.0
     全部 owner_todo 定稿（NFKC、t2s 折叠、横向空白折叠……每条带 decision+reason）。
2. **可靠性底座**（`common/`，47→68 测试）：
   - `ids.py`：`spine_doc_id` / `content_hash` / `normalized_content_hash`
     （规则只从 config 读，代码只硬编码"怎么执行"）；
   - `runs.py`：`start/finish` 事件对 + 非法 stage/status 拒绝 + 坏行报错带行号；
   - `changelog.py`：`append_change(dedupe=True)`（dedupe 键 = doc_id×stage×action×reason×old_hash）、
     跨字段规则表（4 条）、reason↔stage 归属校验、`query_by_doc`；
   - `idempotent.py`：`run_stage`（start→try/except finish(failed)→finish(success) 审计闭环）、
     `fingerprint`、`_outputs_intact` 缓存验货、tmp→rename 原子落盘；
   - `first_seen.py`：`FirstSeenLedger` pending→commit 两段式（时钟不进数据行）；
   - `textstats.py`：char/cjk/utf8 三计数纯函数（Han 区间显式枚举，不收 CJK 标点）；
   - `log.py`（本轮新增）：`setup/get_logger`，LOG_LEVEL 环境变量旋钮。
3. **首个源适配器**（`ingest/wiki.py`）：
   - `download(n_shards)`（hf_hub_download 下载级幂等）→ `iter_docs`（ParquetFile.iter_batches
     流式）→ `map_row`（纯映射：registry 字段零字面量）→ `run()`（run_stage 处理级幂等）；
   - 显式 `PARQUET_SCHEMA`（防 pyarrow 按批推断漂移）；`main()` 接入 log。
4. **容器栈**（`docker-compose.yml` + `docker/`）：7 服务；Spark 自建镜像
   `corpus/spark:3.5.3-s3a`（Dockerfile ADD 两个 s3a jar + chmod 0644）；
   metastore 挂 PG 驱动 + hadoop-aws 3.3.6 对（各容器各对各的 Hadoop 版本）。
5. **验收与冒烟**：`sql/raw_docs.sql`（外部表 + 分区发现 + 验收 GROUP BY）；
   `smoke/m1.py` 五步冒烟；`tests/` 68 个（含跨模块集成审计链、golden record、
   registry 字面量守卫、schema 封闭键守卫、变异测试——假校验位身份证必被拒）。

### 关键决策及理由（代码内注释按编号引用）

- **M1-D1 时钟不进数据行**：first_seen_at 走台账（pending→commit），force 重算字节一致。
  否则 T4 的"同样输入两次产物字节一致"当场作废。证伪：`test_forced_rerun_is_byte_identical`。
- **M1-D2 缓存要验货**：命中 = 指纹 ∧ 目录在 ∧ 逐文件哈希对账。防手改产物被当合格品。
  证伪：篡改产物一字节 → 重跑必须重算（tests/test_idempotent.py）。
- **M1-D3 run 即审计**：run_stage 把 start/finish(failed|success) 写进 runs.jsonl，
  失败留痕再抛异常——「没记录」和「成功」在审计上必须可区分。证伪：集成测试断言事件序列。
- **M1-D4 未检测 ≠ 空 ≠ 有值**：language_mix 不写 {}（撒谎），original_encoding 显式 null
  （断言不可知）。字段取值三分法写进 wiki.py 模块头。证伪：golden record 逐字段钉死。
- **M1-D5 事实单一来源**：license/genre/format 只能来自 source_registry，代码字面量 =
  接缝泄漏，用元测试 grep 源码守卫（tests/test_ingest_wiki.py::test_registry_fields_...）。
- **M1-D6 日志双通道**：stderr 走日志（LOG_LEVEL 可调），stdout 只放结构化结果——
  管道重定向安全。实现 common/log.py。

### 遇到的问题及解决（全部有 dev-lessons 条目）

1. Docker Hub 拉不动 → 镜像源前缀 + retag（#004 死法一）；
2. minio/minio 与 bitnami/kafka 上游撤柜 → bitnamilegacy 替换，minio 数据目录/
   command/建桶方式随之调整（#004 死法二）；
3. FlClash TUN 劫持 DNS 全网失联 → 直连公共 DNS 定位责任层后一键恢复（#004 死法三）；
4. metastore 不认 DB_URI → SERVICE_OPTS 传 javax.jdo.option.*（#005 坑一）；
5. 镜像不带 PG 驱动 → 挂 jar（#005 坑二，boot 检查单预言兑现）；
6. Docker 远程 ADD 落盘 root:600 → chmod 0644（#005 坑三，"存在但不可读"）；
7. 凭据 Provider 类名在两个包 → 用 SDK 的 com.amazonaws.auth.*（#005 坑四）；
8. eventLog 写 s3a 虚拟前缀两连败 → 止损暂关，恢复条件记 backlog（#005 坑五）；
9. Hive 4 拒绝桶根 LOCATION / metastore 无 S3A 凭据拒注册分区 → docs/ 前缀 +
   `manageFilesourcePartitions=false` 文件系统分区发现（sql/raw_docs.sql 头注释四条）。

### 怎么验证的（命令 + 实际输出）

```
$ python -m pytest -q
68 passed in 2.36s

$ python -m ingest.wiki 1          # 首跑
{"cached": false, "rows": 230792, "output_hashes": {"part-00000.parquet": "..."}, ...}
                                   # 重跑同命令 → "cached": true, "rows": null

$ docker compose exec -T spark-master /opt/spark/bin/spark-sql \
    --conf spark.sql.hive.manageFilesourcePartitions=false -f /tmp/raw_docs.sql
wiki   230792
（EXPLAIN 出现 PartitionFilters: (source = wiki) —— 分区裁剪证据）

$ python smoke/m1.py               # 2026-10-30 实跑
✅ S1 docker 栈: 必需服务存活: postgres, minio, metastore, spark-master
✅ S2 单元测试: 68 passed
✅ S3 本地产物: 1 个 part 文件, 511 MB
✅ S4 对象存储: MinIO raw/docs/source=wiki/ 存在: ['part-00000.parquet']
✅ S5 验收 SQL: raw_docs GROUP BY: wiki=230792 行; 分区裁剪证据=有
SMOKE M1: PASS ✅ (5/5)
```

### 微调入口

见 `MANUAL.md` 反查表与微调点索引（M1 行）。高频：分片数（CLI 参数）、输出路径
（`ingest/wiki.py` OUTPUT_DIR）、日志级别（LOG_LEVEL 环境变量）。

### 遗留问题

1. **指纹不含配置**（改 text_normalization 等配置后需手动 force）——WP-01 §2.1
   configs_digest 落地于 M4，落地前此为已知缺陷；
2. common/stage.py 六件套（snapshot_configs/latest_outputs/StagedChangeLog 等）按
   WP-01 契约随 M4 clean 框架一并实装，wiki 届时补 `contract_fixture` 进合同测试；
3. changelog `append_change(dedupe=True)` O(n) 全文件扫描、first_seen 单 JSON 全量重写
   ——百万行级（OSCAR）前换索引结构（dev-lessons/蓝图 §6 已记）；
4. docstring 四段标准（参数/返回/异常/幂等）本轮覆盖 idempotent.py 与 wiki.py 公有函数，
   ids/runs/changelog/first_seen 的存量 docstring 于 M2 顺手补齐；
5. eventLog-on-s3a 恢复方案（哨兵对象）在 backlog；
6. Kafka 已 boot 未验收（W7/M9 的活）。
