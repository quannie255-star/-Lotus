# 开发经验（dev lessons）

> 记录规则：每次「必修修复 / 关键复盘」一篇，编号递增；教训必须来自亲手踩的坑，
> 每篇结尾要有可带走的自查清单或决策规则。复盘在修复当天写，过期不补。

---

## #001 缓存要验货，审计要归账（2026-10-28）

**触发**：W2 阶段 T4 review 提出两项必修——缓存命中不验货、run_stage 不留审计记录。
**涉及**：`common/idempotent.py`、`common/runs.py`、`common/changelog.py`、`tests/test_integration.py`

这次修改表面是修两个 bug，本质是补两个**信任漏洞**：一个是对数据的信任（结果对不对），
一个是对流程的信任（账串不串得起）。后者更值钱，因为它是设计问题。

### 一、存在，不等于正确

**发生了什么**：缓存命中的条件原来只有"输入没变、目录还在"。可目录在、文件在，
都不代表文件内容是上次算出来的那份。

**本质**：缓存的本质是「可证明的等价」，不是「看起来一样」。判断"能不能复用上次的结果"，
需要三样东西：输入指纹（key）、产物清单（value）、产物哈希（verifier）。
大多数项目只做前两样，就会在"有人手改了产物"时静默地把脏数据当成品。

**下次怎么做**：任何缓存/幂等设计，先问"我用什么证据证明这份结果是上次那份"，
把证据写进缓存命中条件，并专门写一条"篡改后必须失效"的测试。

> 类比：从快递柜取件，柜门能打开、包裹还在，都不代表这是你的包裹，得核对单号。

### 二、没有记录，不等于没有发生

**发生了什么**：`run_stage` 原来在 transform 抛异常时直接往外抛，runs.jsonl 里干干净净。
事后看日志，会以为"这次 stage 根本没跑过"，而不是"跑了、失败了"。

**本质**：审计系统里最危险的态是**空白**。「没记录」和「成功」在查询时不加区分，
等于把失败洗成不存在。所以失败、中断、超时都必须是显式事件，而不是"什么都不写"。

**下次怎么做**：先写失败路径，再写成功路径。异常一律包在 `try/except` 里，
记一条 `failed` 再抛出——抛出去是为了让上层知道，记下来是为了让历史知道。

> 类比：监控断了和什么都没发生，在回放里看起来一模一样，但性质完全不同。

### 三、单测全绿，不等于系统正确

**发生了什么**：43 个测试全绿，但三个模块是断开的——runs 不知道 changelog，
changelog 里的 run_id 无处可来。每块砖都是好的，墙没砌上。

**本质**：单元测试证明"函数对"，集成测试证明"链对"。前者是必要条件，不是充分条件。
模块之间**靠一个键相连**（这里是 `run_id`），这个键如果没人传递，单测永远发现不了。

**下次怎么做**：设计时先画一张"谁和谁用同一个键串起来"的图——doc_id 串 spine/changelog，
run_id 串 runs/changelog——再决定每个函数需要哪些参数。写完必须有至少一条端到端测试，
断言"这次运行的产出，全部能归因到这次运行"。

> 类比：医院里病历、检验单、缴费单必须用同一个就诊号；三份单各自都对，号对不上，病人就丢了。

### 四、隐式的"上下文"是技术债

**发生了什么**：改 `transform` 签名，从两个参数变成三个，把 run_id 显式传进去。
这暴露了一个设计缺陷：**谁需要什么信息，就应该由调用方显式注入**。

**本质**：靠全局变量、环境变量、模块级单例"偷偷拿"上下文，短期省事，长期是债——
测试没法隔离，并行会串号，改一处不知道影响谁。显式参数虽然啰嗦，但责任清晰。

**下次怎么做**：设计接口时，把"运行上下文"当成一等公民：`run_id`、路径、版本，
作为参数或一个显式的 `context` 对象传下去；一旦定了签名，改的时候一次改完所有调用点，不留半新半旧。

> 类比：快递单上必须写清收件人，不能靠"反正是同一个小区"。

### 五、方式：先让它"红"，再让它"绿"

这次我没先改代码，而是先想清楚"什么情况下会出错"，写一条会失败的测试（手改产物），
确认问题真的存在，再修。这个顺序看着慢，实际最快——因为它把"我以为有问题"变成了
"我证明有问题"，也顺手留下了防回退的护栏。

### 六、一份可以带走的自查清单

以后做任何「可重跑 / 有缓存 / 要留痕」的系统，问这五个问题：

1. **身份**：这条数据用什么键唯一确定？这个键会漂移吗？
2. **证据**：我凭什么相信这份缓存/结果是上次那份？（哈希、版本、时间戳）
3. **归因**：这次运行产生的每一条副作用，能不能都指回它的 run_id？
4. **失败**：失败、中断、超时，有没有显式记录？还是默默什么都没写？
5. **串联**：有没有一条端到端测试，把两个以上模块真的接起来跑一遍？

### 最后一句

写代码的能力，三个月能练出来；**设计时先问"证据是什么、账怎么对"**，是三年也未必
有人教你的东西。这两处必修属于同一个母题：**任何"省掉一次计算"的优化，都必须先证明
"省得对"；任何"记录一次执行"的设计，都必须回答"怎么把账对上"。**
后面 ingest、dedup 上线时会反复撞上这个母题——今天这两条，就是那时候的骨架。

**导师附注**：这份清单在 W7 会升级成正式设计——Iceberg 的 `meta.runs` 表 + snapshot
机制本质上就是「验货 + 归账」的工业级实现，到时候回来看这篇，你会发现 Iceberg 替你
做了第一、二、四条，你要自己做的是第三、五条。

---

## #002 compose 三必修：镜像缺 jar、凭据进了 git、元数据两处未知（2026-10-28）

**触发**：compose 第一版 review，点出三个 boot 前必须处理的问题。
**涉及**：`docker-compose.yml`、`docker/spark/Dockerfile`、`docker/spark/conf/spark-defaults.conf`

### 一、官方镜像不是"官方就齐全"

**发生了什么**：`apache/spark:3.5.3` 不带 S3A 的 jar，第一次访问 `s3a://` 必然
`ClassNotFoundException: S3AFileSystem`。

**本质**：Spark 的 Hadoop 集成是"插件式"的——`fs.s3a.impl` 只是告诉 Hadoop 去哪找实现类，
类本身得另装，且**版本必须和镜像内置 Hadoop 对齐**（Spark 3.5.x 捆绑 Hadoop 3.3.4 →
`hadoop-aws:3.3.4`，它声明的 `aws-java-sdk-bundle` 是 `1.12.262`）。版本错位不会报"版本错"，
而是报 `NoSuchMethodError` / `ClassNotFoundException` 这类迷惑性错误。

**下次怎么做**：遇到"框架能跑但缺某个 backend"，先查镜像里有没有对应 jar，再查它依赖的
Hadoop/AWS SDK 版本；把补丁做成 Dockerfile（可复现），不要靠人肉挂卷。

### 二、凭据纪律要贯穿到配置文件，不只 compose

**发生了什么**：compose 层用了 `${POSTGRES_PASSWORD:-...}`，但 `spark-defaults.conf` 里
明文写着 MinIO 的 access/secret，而它是被 git track 的。

**本质**：secret 只需要"活在一个地方"——运行时的环境变量。配置文件里出现明文，就等于
把它写进了 git 历史，删掉也还在历史里。

**下次怎么做**：配置里只放"哪来的"（`EnvironmentVariableCredentialsProvider`），
不放"是什么"；compose 负责注入 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`。
自查方法：`git grep -nE "(password|secret|token|key)" -- '*.conf' '*.yml'`。

### 三、未知数不要猜，列成 boot 检查单

**发生了什么**：Metastore 是否内置 PostgreSQL JDBC 驱动、schema 是否自动初始化——
这两个只有真起来才知道。

**本质**：这类问题不该在 review 里"推测"，而该在 boot 时用日志回答，并把答案写进文档。
文档的价值在于：下次换机器/换镜像版本时，你知道要重新验证哪几条。

---

## boot 检查单（2026-10-29 已回填；boot 一次跑通全链路）

| # | 检查 | 命令 | 通过标准 | 实际 |
|---|---|---|---|---|
| 1 | Spark 镜像里的 S3A jar | `docker compose run --rm spark-master ls /opt/spark/jars \| grep -E "hadoop-aws\|aws-java-sdk"` | 两个 jar 都在 | ✅ 通过，但 ADD 落盘 root:600 需 `chmod 0644`（见 #005 坑三） |
| 2 | Metastore 的 JDBC 驱动 | `docker compose logs metastore \| grep -i "no suitable driver"` | 无输出 | ❌→✅：镜像确实不带，挂 `postgresql-42.7.4.jar` 修复（见 #005 坑二） |
| 3 | Metastore schema | `docker compose exec metastore schematool -dbType postgres -info` | 返回版本 | ❌→✅：先撞 Derby 引擎，改 `SERVICE_OPTS` 后 `initSchema` 成功（见 #005 坑一） |
| 4 | MinIO bucket | `docker compose exec minio mc ls local` | 有 warehouse / logs | ✅ `warehouse`+`logs` 由 `MINIO_DEFAULT_BUCKETS` 自动建；`raw`/`staging` 未自动建，ingest 前补 |
| 5 | 全链路冒烟 | `spark-sql -e "CREATE DATABASE IF NOT EXISTS smoke; SHOW DATABASES;"` | 输出含 smoke 且 warehouse 有目录 | ⚠️→✅ 读链路（SHOW→metastore→PG）✅；写链路走 **datasource 路径**通过：`CREATE TABLE ... USING parquet LOCATION 's3a://warehouse/smoke_t'`，INSERT/SELECT 2 行全通，mc 确认桶内 `smoke_t/`。Hive-managed 的 `CREATE DATABASE`（让 metastore 去 mkdir s3a）仍报错，属已知差异（见 #005 坑五），我们的真实用法（引擎写数据、W7 Iceberg）不依赖它 |

**规则**：跑完一条就把「实际」填上；失败就在下面加一篇 #005，写清命令、现象、修法。

---

## #003 批量改文件别用 PowerShell 的 `-replace`（2026-10-28）

**现象**：用 `(Get-Content -Raw) -replace ... | Set-Content` 把 compose 里的 `${VAR:-default}`
批量改成 `${VAR:?msg}`，结果文件变成乱码、多行被合并、开头多了 BOM，
`docker compose config` 直接报 YAML 解析错误。

**三个原因叠在一起**：
1. PS 5.1 的 `Get-Content` 不带 `-Encoding` 会按 ANSI/GBK 读 UTF-8 文件 → 中文变乱码；
2. `-replace` 的**替换串**里 `${...}` 被当成 PowerShell 变量/捕获组语法 → 内容被吞；
3. `Set-Content -Encoding UTF8` 在 5.1 写出 BOM → YAML 解析器把 BOM 当非法字符。

**修法**：整文件重写，别做行内批量替换；必须批量时用 Python（明确 `encoding="utf-8"`）。
**自查**：改完跑 `docker compose config --quiet`，再看文件头三字节是否 `EF BB BF`。

---

## #004 镜像供应链的三种死法（2026-10-29，导师 boot 代查记录）

**触发**：W1 boot 第一次执行，三小时内连撞三个互不相同的环境故障。
**涉及**：`docker-compose.yml`（minio/kafka 镜像替换）、Docker Desktop、FlClash

### 死法一：拉不动——网络层

**症状**：`docker pull` 报 `Get "https://registry-1.docker.io/v2/": request canceled while waiting for connection`。
**诊断**：Docker Hub 国内直连不可达；守护进程的 pull 不走系统代理（FlClash 开着也没用，
报错里那句 "Docker Desktop has no HTTPS proxy" 就是明证）。
**修法**：镜像源前缀拉取 + 本地 retag（`docker pull docker.m.daocloud.io/library/postgres:16-alpine`
→ `docker tag` 回原名）。不改 Docker Desktop 配置，侵入最小。
**备选源实测**（2026-10-29）：daocloud 对部分非 library 镜像返回 denied；1ms.run 对上游已删除
的镜像会明说「官方仓库可能已删除该镜像」——**报错文案本身是诊断信息**。

### 死法二：上游没了——供应链层（最隐蔽）

**症状**：`minio/minio:RELEASE.2024-10-13T13-34-11Z` 与 `bitnami/kafka:3.7.1` 在多个镜像源
一律 not found / denied，但 postgres、spark 等一切正常。
**诊断**：不是网络，是**上游真的删了**——MinIO 2025 年从 Docker Hub 整仓撤柜；
Bitnami 目录迁移把老版本挪进 `bitnamilegacy/*` 冻结仓。教训：`image:` 里钉的 tag
不是永久存续的承诺，「钉版本」防的是漂移，防不了下架。
**修法**：
- kafka → `bitnamilegacy/kafka:3.7.1`：`KAFKA_CFG_*` 环境变量与 `/bitnami/kafka` 卷路径
  完全兼容，compose 只改镜像名；
- minio → `bitnamilegacy/minio:2025.5.24`：同源 MinIO，但 ① 数据目录是
  `/bitnami/data/minio` 不是 `/data`；② 不要自定义 `command`（Bitnami 入口自带
  server+console）；③ 顺手用 `MINIO_DEFAULT_BUCKETS: "warehouse,logs"` 自动建桶。
**自查规则**：换镜像供应商时，先 `docker manifest inspect` 探活，再 diff 两家的
环境变量/卷路径/入口命令——**"同款软件"不等于"同款镜像契约"**。

### 死法三：本机 DNS 被劫持后噎死——客户端层

**症状**：五分钟前还能拉的镜像源突然 `lookup docker.1ms.run: no such host`，
且 baidu.com 也解析失败；`nslookup` 显示 DNS 服务器是 `192.168.210.1`。
**诊断**：FlClash TUN 虚拟网卡接管了系统 DNS，随后其 DNS 服务挂了。
关键鉴别动作：`nslookup baidu.com 223.5.5.5` 直连公共 DNS 正常 → 网络没断，
只有本地解析器死了 → 指向 TUN 网卡，而不是路由器/运营商。
**修法**：重启/关闭 FlClash TUN。十秒恢复。
**带走规则**：全机断网类故障，先「绕过本地解析器直连公共 DNS」定位责任层
（网络 vs DNS vs VPN 劫持），再动手——这一步鉴别避免了我们去重启 Docker、
改 DNS 配置等所有错误动作。

### 本篇口试（owner 补答）

1. `bitnamilegacy/kafka` 只改镜像名就能跑，`bitnamilegacy/minio` 却要改数据目录、
   删 command、加环境变量——为什么两个"同厂迁移"的镜像待遇不同？这暴露了
   镜像替换时应该先检查什么？
2. `MINIO_DEFAULT_BUCKETS` 帮我们省了手工建桶，但它违背了哪条我们自己的设计原则
   （提示：想想要素在哪边定义）？可接受吗，为什么？

---

## #005 Metastore 三连坑：SERVICE_OPTS、PG 驱动、ADD 权限（2026-10-29）

**触发**：W1 boot 打通 metastore + spark + s3a 时，在 Hive 这一侧连撞三个坑。
**涉及**：`docker-compose.yml`（metastore 服务）、`docker/spark/Dockerfile`

### 坑一：镜像不认 `DB_URI`，JDBC 连接要走 `SERVICE_OPTS`

**症状**：给 `apache/hive:4.0.0` 传了 `DB_URI`/`DB_USER` 却无效，`schematool` 拿默认
Derby 连接去跑 postgres 初始化脚本，报 `Syntax error: Encountered statement_timeout`
（PG 语法被 Derby 引擎解析，驴唇不对马嘴）。

**本质**：这个镜像的 entrypoint 不读 `DB_URI` 这类"直觉"变量；JDBC 连接只能通过
`SERVICE_OPTS` 里的 `javax.jdo.option.*` 系统属性（`ConnectionDriverName` / `ConnectionURL`
/ `ConnectionUserName` / `ConnectionPassword`）传给 Hive。

**修法**：compose 的 metastore 服务用 `SERVICE_OPTS` 写全四个 `-D` 属性（见 compose 注释）。

**带走**：换官方镜像，先读它的 entrypoint/README 看它**认哪些变量**，别按同类产品经验猜；
报错里"PG 语法被另一引擎解析"这类症状，第一反应是"连错库了"，不是"SQL 写错了"。

### 坑二：镜像不带 PostgreSQL JDBC 驱动

**症状**：`ClassNotFoundException: org.postgresql.Driver`。

**本质**：Hive 官方镜像默认面向内嵌 Derby，不打包 PG 驱动——这正是 #002 检查单第 2 条
预留的悬念，boot 揭盅：**确实不带**。

**修法**：挂 `postgresql-42.7.4.jar` 到 `/opt/hive/lib/postgresql.jar`（compose 已从注释改实挂）。

**带走**：未知数不要猜，boot 用日志回答，答案写回文档——这次 #002 的"待验证"表兑现了它存在的意义。

### 坑三：Docker 远程 ADD 落盘 `root:600`

**症状**：jar 在 `/opt/spark/jars/` 里 `ls` 可见，但 JVM 仍报 `ClassNotFoundException`。

**本质**：Docker 对远程 URL 的 `ADD` 默认以 `root:600` 落盘；而 `apache/spark` 镜像以
`USER spark` 运行，600 权限 = 属主 root 之外无人可读 → jar "存在但 spark 用户不可读"。
`ClassNotFoundException` 的元凶不是"缺文件"，而是"没权限读文件"。

**修法**：Dockerfile 里 `ADD` 之后加 `RUN chmod 0644 ...` 再 `USER spark`（见 compose 注释）。

**带走**：往镜像塞文件后，必问"运行用户是谁、文件属主与权限是谁"；`ClassNotFound` 先
`ls -l` + `id` 排除权限/属主，再怀疑缺文件——省一次瞎猜。

### 坑四：凭据 Provider 类名——同名概念在两个包里，只有一个存在

**症状**：S3AFileSystem 加载成功后，下一层报 `ClassNotFoundException:
org.apache.hadoop.fs.s3a.EnvironmentVariableCredentialsProvider`。

**本质**：hadoop-aws 3.3.4 里**没有**这个名字的类（那个名字在别的 Hadoop 版本线里）；
"从环境变量读 AWS 凭据"的实现在 AWS SDK 包里：`com.amazonaws.auth.EnvironmentVariableCredentialsProvider`。
`fs.s3a.aws.credentials.provider` 接受任何 AWSCredentialsProvider 实现，安全语义相同（密钥仍只走环境变量）。

**带走**：配置一个"类名"之前，先在对应的 jar 里确认它存在（`unzip -l` / javap）；
报 ClassNotFound 时区分三种死法：真没有（#005 坑三）、有但读不了（#005 坑三 chmod）、
名字记错了（本坑）。

### 坑五：eventLog 写 s3a 的虚拟前缀——两连败后止损

**症状**：`spark.eventLog.dir=s3a://logs/spark-events` 报 `FileNotFoundException`（S3 的
"目录"只是前缀，不存在实体）；改成桶根 `s3a://logs/` 又撞 `path must be absolute`（S3Guard 校验）。

**修法**：`spark.eventLog.enabled=false` 暂关，记入 backlog——方案是初始化时在
`logs/spark-events/` 前缀下放一个 `.keep` 哨兵对象再启用。**W1 验收不依赖它，不在基础设施周耗时间。**

**带走**：锦上添花的功能（可回放 event log）挡住主线（全链路冒烟）时，止损优先级 >
死磕；但止损必须留下「为什么关 + 恢复条件 + 方案」，否则就是遗忘，不是取舍。

### 本篇口试（owner 补答）

1. `apache/hive:4.0.0` 为什么非走 `SERVICE_OPTS` 的 `-Djavax.jdo.option.*`，而不是
   `DB_URI` 这类环境变量？这暴露了"官方镜像的配置契约"有什么坑？
2. `ADD` 远程 URL 落盘的 `root:600` 权限坑，除了 `chmod`，还有哪两种更稳的写法？
   （提示：COPY 本地文件；或把下载放进 `RUN curl && chown`）

---

## #006 T5 ingest 四个坑：批格式、桶根、metastore 凭据、会变的时钟（2026-10-29）

**触发**：写 `ingest/wiki.py` 并跑 W1 终极验收（上传 MinIO + 外部表 + `GROUP BY source`）。
**涉及**：`ingest/wiki.py`、`common/first_seen.py`、`sql/raw_docs.sql`、`docker-compose.yml`（待改）

### 坑一：`iter_batches` 默认给的不是 dict，连"迭代它"都不行

**症状**（实测）：`next(pf.iter_batches(batch_size=2))` 是 `RecordBatch`；
`batch[0]` 拿到的是**一整列**（`StringArray`），`batch[0]["text"]` 报
`TypeError: 'str' object cannot be interpreted as an integer`；
而 `for row in batch` 迭代出来的也不是行，是一列一列的 `StringArray`——
**这段代码不报错也能跑，只是每一"行"其实是一列**。
**本质**：`RecordBatch` 是列存批次（Arrow 的行列模型里它面向列），
`to_pylist()` 才是"一批行字典"。名字都叫 batch/row，语义差一层。
**修法**：`for row in batch.to_pylist(): ...`，并且 batch 大小显式给（`BATCH_SIZE=10_000`），
让内存上界可推理。
**带走**：流式 API 先问清"它给你的单位是什么"（batch / row / column）；
更狠的一条：**如果迭代一个批次还能继续跑，说明它可能以错误粒度在跑**——
沉默的错误比报错贵。

### 坑二：Hive 不让把"桶根"当表位置

**症状**：`CREATE EXTERNAL TABLE raw_docs ... LOCATION 's3a://raw/'` 报
`InvalidObjectException: raw_docs location must not be root path`。
**本质**：Hive 4 对表位置有合法性校验，"文件系统的根目录"被硬性拒绝（不只是"不太好"）。
**修法**：数据放多一级前缀 `s3a://raw/docs/source=wiki/`，表 `LOCATION 's3a://raw/docs/'`；
`source=...` 仍是目录名，分区裁剪不受影响。
**带走**：设计目录布局时，"想怎么分层"和"引擎允许怎么分层"是两回事——
先拿最小 DDL 试一次，再往里面灌 500MB 数据。

### 坑三：metastore 没有 S3A 凭据，Spark 会**静默降级**

**症状**：建表"成功"了，但日志里一行 `WARN HiveExternalCatalog: Could not persist ... in a
Hive compatible way. Persisting it into Hive metastore in Spark SQL specific format.`
根因是 `MetaException: got exception: AccessDeniedException s3a://raw/docs:
NoAuthWithAWSException`；同一根因让 `MSCK REPAIR`、`CREATE DATABASE`（要 mkdir）全挂。
**本质**：Spark 对"Hive 兼容落库失败"是**降级而不是报错**——表在 metastore 里只有
`spark.sql.sources.*` 属性，Spark 自己能查，Hive/Trino 看到的是一张没有列的表。
"查询能跑"因此不是通过标准，**日志里的 WARN 才是**。
**修法**（infra，进 backlog）：给 metastore 注入 `AWS_ACCESS_KEY_ID/SECRET` + `fs.s3a.*`
并确认镜像里有 `hadoop-aws`；raw 层临时用
`--conf spark.sql.hive.manageFilesourcePartitions=false` 走文件系统分区发现。
**带走**：验收要同时看"结果对不对"和"有没有降级 WARN"；降级是静默的技术债，
它不会在 CI 里变红，只会在换引擎的那天炸。

### 坑四：一个"每次运行都变"的字段，会毁掉字节级幂等

**症状**：`first_seen_at = now()` 让同一批输入两次跑出的 parquet 字节不同，
T4 承诺的"同样输入 → 同一份字节"当场失效，`force` 重跑也无法复现。
**本质**：幂等要求"输出只由输入决定"；凡是把**运行时刻**写进产物的字段
（`now()`、`run_id`、`hostname`），都是把不确定性放进数据。
**修法**：`first_seen_at` 落台账（`data/meta/first_seen/wiki.json`，`doc_id → 首次时间`），
重跑沿用旧值；`ingest_run_id` 干脆不写进数据行，归因走 runs.jsonl + manifest。
**带走**：往数据里加任何一个字段前先问"它会不会随运行而变"；
会变的东西放元数据/台账，别放 spine。

### 一篇可以带走的自查清单

1. **批格式**：流式读的 API 返回的是 batch 还是 row？切换点在哪一行？
2. **位置合法性**：表位置/分区路径，引擎真的接受吗（先最小 DDL 试跑）？
3. **降级 WARN**：结果对，但有没有"换个引擎就读不了"的静默降级？
4. **会变的字段**：产物里有没有 `now()` / `run_id` / 主机名？字节还能复现吗？
5. **估算 vs 实测**：任务书说"约 30 万行"，实测 230,792——先量再定容量。

### 最后一句

这一轮的四个坑分两类：**接口语义**（坑一、坑二）与**隐藏状态**（坑三、坑四）。
接口语义的坑，代价是一次报错；隐藏状态的坑，代价是"看起来通过了"——
metastore 凭据缺了不报错、时钟进了数据不报错，它们只在你换引擎、换机器、要求复现的那天出现。
**验收的真正标准不是"跑通了"，是"我知道它为什么能跑通，以及它在什么条件下会悄悄不成立"。**

