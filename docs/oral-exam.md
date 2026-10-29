# 口试答卷（③ + ⑤）

> 规则：每题答案控制在三句话内，先给结论再给依据。下面的「依据」只是给自己复盘的出处，不该念出来。

---

## ③-1　run 崩在 start 后、finish 前，日志永远 running，读端怎么判死活？v2 方案？

**答**：只追加的日志里，「真在跑」和「崩在 start 后」是同一种状态——没有新事件这件事本身不携带信息，所以读端不能把"最后一条事件"当真相。v2 一句话：start 事件带租约（心跳时间 + 期望完成时刻），读端用「当前时间 − 最后心跳 > 阈值」判死，再补写一条 `timeout` / `reaped` 事件完成状态转移，日志仍然只追加。

**依据**：`common/runs.py` 目前只有 start/finish 两类事件，没有任何超时判定——这正是 v2 要补的那块。

---

## ③-2　fail-fast 对审计链对、对看板为什么可能错？

**答**：审计链要的是"事实不可丢"——写入失败必须炸，否则那条变更证据永久缺失，事后无法自证；看板是旁路遥测，每次打点失败都 fail-fast，一次监控故障就会反噬主流程，而聚合视图丢一条只影响精度、不影响结论。本质区别是**强一致证据 vs 弱一致观测**：前者丢一条等于数据损坏，后者丢一条等于采样误差。

**依据**：`common/changelog.py` 的 `append_change` 校验不过就抛 `ValueError`（该炸）；而看板类埋点失败应当降级为告警日志，不阻断 stage。

---

## ③-3　删掉所有 `encoding="utf-8"` 后，Windows 上会发生什么、现象是什么？

**答**：Windows 的 Python 默认用 locale 编码（中文系统 = cp936/GBK），按 GBK 去解 UTF-8 字节，第一步就抛 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xef ...`，现场通常在 `json.load` / `read_text`；写日志时中文会被 GBK 编码，遇到 GBK 表示不了的字符抛 `UnicodeEncodeError`，若用了 `errors="replace"` 则不崩、落盘成 `?` 或 `鏁版嵁` 这种 mojibake。现象不是"代码有 bug"，而是**同一份文件在 Linux 上正常、在 Windows 上是乱码**。

**依据**：`common/ids.py` 的 `load_norm_config` 里 `CONFIG_PATH.open(encoding="utf-8")`——去掉它，读 config 就炸在这一行。

---

## ⑤-V1　NFKC 已折叠全角标点，punctuation 规则是不是白写？

**答**：不是。NFKC 只折叠**有兼容分解**的字符：全角 ASCII 变体（FF0C、FF01、FF1A、FF1B、FF08…）、U+2026 省略号、U+3000 表意空格；CJK 与通用标点在 Unicode 里没有兼容分解，NFKC 原样保留。实测留到最后的正是 `、。《》〈〉「」“”‘’—–‒―⋯·・` 这一批，punctuation 就是补这一层，凡 NFKC 已折叠的都不该再进 map，否则是重复劳动。

**依据**：`configs/text_normalization.json` 的 `punctuation.map`（15 条）；用 `unicodedata.normalize("NFKC", ch)` 逐字符验证过。

---

## ⑤-V2　规则升级导致哈希变化，怎么做到不重写全库、其他阶段零改动？

**答**：`normalized_content_hash` 是纯派生字段，升级时只重算 dedup 阶段的它，并把 `normalized_hash_version` 写成新版本，旧行保留旧版本值——两代并存即可对比，不必重写全库。不动的三块：spine 的 `doc_id`（只由 source + source_id 算）、`content` 与 `content_hash`（不含任何规范化）、以及 runs / changelog 历史。其他阶段零改动，是因为它们只读 `doc_id` 和 `content`、根本不读这个哈希；要全量生效就用后台任务按 version 分批回填，别阻塞发布。

**依据**：`schema/unified_schema.json` 的 `dedup.normalized_hash_version`；`common/ids.py` 里三个函数互不依赖。

---

## ⑤-V3　简→繁是多映射，繁简折叠对去重的风险是什么？W4 怎么量化？

**答**：风险是"过度合并"——一个简体字对应多个繁体（发→發/髮、后→後/后、干→乾/幹），t2s 会把本来有区别的写法折成同一串，产生假重复而误删，短文本尤其危险；反向的不可逆损失是回不到繁体。W4 用双样本量化：一组构造歧义字最小对照对（同一简体对应 2–3 个不同义繁体词），一组真实语料，分别跑"开/关折叠"两遍 dedup，比较**假重复率**（抽样人工判定误删）与**漏重率**，用误删率卡阈值，产出映射白名单/黑名单回填 config。

**依据**：`configs/text_normalization.json` 的 `traditional_to_simplified_mode = "t2s"`；decision 里已写明"多映射风险由 W4 量化后回填白名单"。

---

## ⑤-V4　spark-submit 在 Airflow 容器里 local 模式跑，解析 `minio` 的是哪个容器？driver 改宿主机会怎样、怎么改？

**答**：local 模式下 driver 和 executor 都在 Airflow 容器那个 JVM 里，所以解析 `minio` 的是 **Airflow 容器自己的 DNS**（Docker 内嵌 DNS 127.0.0.11 把 compose 服务名解析成 minio 容器 IP），S3A 拿到的就是这个 IP。driver 搬到宿主机后，宿主机不在 compose 网络里，`minio` 直接 NXDOMAIN、或连到未发布的端口，任务在 S3A 初始化阶段就挂。改法三选一：compose 给 minio 发布 9000/9001 并让宿主机 driver 用 `localhost:9000`；或给 driver 加 `--add-host minio:host-gateway`；或让 driver 也跑在同一 network 的容器里（提交到 `spark://spark-master:7077`）。

**依据**：`docker-compose.yml` 目前还是空占位，这道题是 compose 课的前置。

---

## 提交方式

把本文件挂到对应 commit 的正文里（或保留在 `docs/` 下并让 commit 引用它），不要只留在聊天记录里。

---

# compose 课（第 1–2 题）

## ① Hive Metastore 为什么需要 PostgreSQL？「元数据」和 MinIO 里的「数据」差在哪？

**答**：Metastore 存的是**目录**——有哪些库、表、分区、字段类型、存储格式、表数据在哪个 `s3a://` 路径、serde 参数、统计信息；这些要被 Spark、Trino、Hive、Airflow 同时访问，需要事务、行锁、唯一约束、崩溃恢复，是典型的 OLTP 负载，所以必须是一个共享的服务端数据库。早期默认的嵌入式 Derby 是"单进程文件锁"，一个 JVM 打开后别的进程打不开，多客户端场景根本走不通；PostgreSQL 用一行 `DB_URI` 就解决了并发、约束与备份。

**元数据 vs 数据**：MinIO 里是**实物**（Parquet/ORC 文件、原始 PDF/HTML），Metastore（PostgreSQL）里是**卡片目录**（表叫什么、字段什么类型、文件在哪、多少行）。丢了 MinIO，目录还在但指向空气；丢了 PostgreSQL，文件都还在，却不知道该读哪些、按什么 schema 读——分区表还能靠 `MSCK REPAIR TABLE` 从路径反推救回一部分，表定义、类型、统计信息基本救不回来。所以 PostgreSQL 是唯一真源、必须持久化且要备份，MinIO 是数据本体。

## ② 六个容器谁要持久化卷、谁挂了随便重启？

**答**：

| 容器 | 卷 | 一句话理由 |
|---|---|---|
| PostgreSQL | **必须** | 唯一真源：metastore 元数据 + airflow 元数据库；丢 = 重建地狱 |
| MinIO | **必须** | 数据本体：warehouse / raw / staging |
| Kafka | **必须** | 消息日志就是状态，未消费的 topic 数据随容器一起没 |
| Airflow | **部分** | 容器无状态（元数据在 PG），但 DAG 是代码、日志是排障证据，两者要卷 |
| Spark | **不需要** | master/worker 无状态，挂了重启；正在跑的 job 靠 T4 幂等重跑 |
| Metastore | **不需要** | 状态全在 PG + MinIO，容器重启只是重新连库 |

判断标准只有一句：**状态在哪，卷就在哪**。容器是牲口不是宠物；有资格进卷的，是"丢了就不可再生"的东西——数据、消息、代码、证据。

---

# 第一版 compose 设计要点（待 review）

- **服务名即主机名**：`minio:9000`、`thrift://metastore:9083`、`postgres:5432` 全靠 Docker 内嵌 DNS；改服务名等于同时改 spark conf、metastore 的 `DB_URI`、airflow 的连接串（这就是 V4 那题的续篇）。
- **端口只发布 3 类**：minio 9000/9001、spark-ui 8080、airflow 8081（避开 Spark UI）；postgres 与 metastore 不发布，调试走 `docker compose exec`。
- **一个 Postgres 两个逻辑库**：`metastore` 与 `airflow` 分开，备份/恢复互不牵连，也让"元数据 vs 数据"的边界在库级别可见。
- **健康检查 + depends_on condition**：metastore/airflow 等 postgres healthy 再起，避免"起来了但连不上库"的假启动。
- **Kafka 用 KRaft 单节点**：没有 ZooKeeper，少一个容器、少一处状态；代价是单点，本机开发够用。
- **Spark 的 s3a 与 metastore uris 写在 `docker/spark/conf/spark-defaults.conf` 里**，挂载进容器；否则 Spark 根本不知道 `minio` 这个主机名对应谁。
- **凭据走 `.env`**（已 gitignore），compose 里只保留开发默认值。

