# 未关账任务总清单

> 更新：2026-10-29 ｜ 来源：本轮 review 汇总（按依赖顺序重排）
> 图例：⬜ 未开始 · 🟨 进行中 · ✅ 关账 · 🔶 部分完成
> 原则：**①→④ + 口试是本周硬门禁，⑥ 并行清掉，③⑤ 不再欠新账。交一批评一次，节奏不变。**

---

## 0. 本批次门禁（关键路径）

两条互不阻塞的并行链 + 一条环境链，三者全部关账才过批次门禁。

| 链 | 路径 | 产出物 | 阻塞关系 |
|----|------|--------|----------|
| **A** | ① → ④ → ⑤ | `configs/text_normalization.json` 定稿 + `common/ids.py` + 5 条验收测试 + 口试 V1–V4 | ① 阻塞 ④ |
| **B** | ② → ③ | `common/{runs,changelog}.py`（或 `audit.py`）+ 5 类测试 + `pytest -v` 绿灯 + 口试三题 + commit | ② 阻塞 commit 与 ③ |
| **C** | ⑥ | HF token + 小公开集拉取验证输出 | 与 A/B 并行 |

```text
①text_norm ──> ④ids.py ──> ⑤口试V1-V4 ─┐
②T2落盘+测试 ──> ③口试三题 ──> commit ──┼──> 本批次关账 ──> T3 / T4 / compose 课
⑥HF链路（并行，卡超 1 天启动 plan B）──┘
```

---

## 一、门禁任务（按顺序做：1 → 2/3 并行 → 4 → 5）

### ① text_normalization.json 定稿　✅（已解阻塞 ④）

- [x] 核心决策 1：**NFKC**（去重/检索召回优先；保真由 raw 承担）
- [x] 核心决策 2：**开繁简折叠，方向固定 t2s**（繁→简多对一、歧义小；简→繁才是一对多高风险）
- [x] `case_folding = lower`；空白折叠 + trim + LF 行尾；零宽（200B/200C/200D/FEFF）与 C0/C1 控制符剔除
- [x] `punctuation` 保留，只映射 NFKC 不动的 CJK/通用标点（、。》《 “” ‘’ — – ‒ ― ⋯ · ・）
- [x] `hash.truncate = null`（完整 64 位，拒绝 16 位截断）
- [x] `version`：0.0.1-draft → **1.0.0**；`status` 字段移除
- [x] 新增 `schema/text_normalization.schema.json`；`$schema` 由 `../schemas/`（悬空）改为 `../schema/`
- [x] 实测核对 NFKC 折叠范围（`unicodedata`）：会折叠 `，！？：；（）Ａ２　…`；不折叠 `、。《》“”‘’—–‒―⋯·・` → 印证 punctuation.map 不是白写
- [x] `jsonschema.validate(configs/text_normalization.json, schema/text_normalization.schema.json)` 通过

> 遗留（已全部处理）：
> - [x] 字段名 `owner_todo` → `decisions`（决策台账，11 条；不再有待办标记）
> - [x] `hash.note` 写死口径：`content_hash` 与 `normalized_content_hash` 都只对 content 的 utf8 字节取哈希，**不碰 raw 文件字节**（与 `unified_schema.json` policy 对齐）
> - [x] 新增 `pipeline_order`（7 步显式顺序，schema 用 `enum` + `uniqueItems` 锁定）
> - [x] 补 `control_chars_keep = [\n \t \r]`：原决策"剔除 C0/C1 但保留换行制表"在实现上自相矛盾，已机器可读化
> - [x] 补 `whitespace.collapse_scope = horizontal`：保守保留换行结构；若日后改 `all` 必须升 version
> - 复核项：`updated_at` 已改为 2026-10-28（原 2025-06-01 早于仓库首次提交）

### ② T2 收尾（落盘 + 测试 + commit）　🟨 代码与测试已完成，待 commit + 口试

- [x] 落盘 `common/runs.py` + `common/changelog.py`（没拆 `audit.py`：runs 记运行生命周期、changelog 记数据变更，两个关注点不该合）
- [x] 6 条 review 意见全部修掉：
  - [x] `query_by_doc` 补上
  - [x] 自动打点策略统一：`append_change` 自动补 `changed_at` / `review_status`
  - [x] `new_run_id` 校验 stage 枚举（读 `reason_registry.json`）
  - [x] `finish` 的 status 枚举校验 + 未 start/重复 finish 拒绝
  - [x] 跨字段检查升级为规则表 `CROSS_FIELD_RULES`
  - [x] 坏行报错带 `文件:行号`
- [x] 测试 15 个（runs 7 + changelog 8：缺 required / 非法 enum / quarantine 跨字段 / 坏行行号 / start-finish 对读回 / reason 归属 / 去重）
- [ ] `commit` + 贴 `pytest -v` 绿灯输出（署名与 commit message 归 owner）

### ③ T2 口试三题　🟨 答案已成稿 → `docs/oral-exam.md`，待随 commit 提交

1. run 崩在 start 之后、finish 之前，日志永远停在 `running` —— **读端怎么判死活？** v2 方案一句话。
2. fail-fast 对**审计链**是对的、对**看板**为什么可能错？两种场景的本质区别。
3. 所有文件句柄的 `encoding="utf-8"` 删掉后，Windows 上会发生什么、**现象**是什么（不是理论，是现象）。

### ④ T1 — `common/ids.py`　✅（11 个测试全绿）

- [x] `spine_doc_id` = `sha256(source + ":" + source_id)`
- [x] `content_hash` = `sha256(utf8(content))`（无规范化）
- [x] `normalized_content_hash` —— 读 config，按 `pipeline_order` 执行，零硬编码规则
- [x] 拍板：完整 64 位（`hash.truncate = null`）
- [x] 验收测试 5 条 + 边界：幂等 / 全半角 / 大小写空白 / 简繁 / 不同内容 / 标点 / 换行保留 / 哈希形态

### ⑤ 补交口试 V1–V4　🟨 答案已成稿 → `docs/oral-exam.md`，待随 T1 一起提交

1. NFKC 已折叠全角标点，`punctuation` 规则**是不是白写**？若不是，它真正还要处理哪类字符。
2. 规则升级导致哈希变化，**怎么做到不重写全库、其他阶段零改动**？说清：哪个字段变、哪张表重算、哪些表不动。
3. 简→繁是**多映射**，繁简折叠对去重的风险是什么？W4 设计什么实验量化。
4. spark-submit 在 Airflow 容器里以 local 模式跑，**解析 `minio` 的是哪个容器**？driver 改跑宿主机上会发生什么、怎么改？

---

## 二、环境欠账（与上面并行，互不阻塞）

### ⑥ HF 链路收尾　⬜

- [ ] Clash 开代理
- [ ] 建 / 读 token（或手写进 `~/.cache/huggingface/token`）
- [ ] 网页同意 OSCAR-2201 条款
- [ ] 新终端 `huggingface-cli login`
- [ ] 拉任一小公开集验证 + 贴输出

> **触发条件：卡超一天 → 启动 plan B**：先跑 wiki + 新闻 + PDF 三源，OSCAR 后补。

### ⑦ 顺手小账　✅

- [x] 建 `requirements.txt`，固定：`pytest==9.0.2` `jsonschema==4.26.0` `zhconv==1.4.3` `datasets==5.0.1` `huggingface_hub==0.36.2`
- [x] 建 `docs/source_metadata_key_registry.md`（已登记 4 个源的 10 个 key，不是空占位）

---

## 三、已排队（门禁通过后依次解锁）

- [x] **T3** `clean/pii.py`：身份证 MOD 11-2 + 出生日期校验、银行卡 Luhn、手机号、邮箱；长数字串防误匹配；`redact()` 返回命中类型供 changelog 使用（8 个测试）
- [x] **T4** `common/idempotent.py`：输入指纹 → 命中前**重算产物哈希验货**（必修 1）、临时目录 + 原子替换、manifest 记产物哈希、重跑字节一致、失败也留 failed 事件（8 个测试）
- [x] **跨模块集成测试**（必修 2）：一次 stage 运行 → runs 里 start/finish 对 → changelog 记录带同一 run_id → 重跑零新增、标 cached
- [x] **compose 课**：三题已答（V4 + compose ①②），第一版 `docker-compose.yml` 已交，review 后进入第二版
  - ① Spark 靠什么找到 MinIO、服务名为什么重要 ✅（V4）
  - ② Metastore 为什么需要 PostgreSQL、元数据和数据的区别 ✅
  - ③ 六个容器谁要持久化卷、谁随便重启 ✅
- [x] compose review 三必修：S3A jar 走 Dockerfile 预置、spark 凭据改环境变量、metastore 两处未知数列入 boot 检查单
- [x] **boot 测试**：本机 Docker daemon 未运行时留的检查单已执行，五项全部回填（`docs/dev-lessons.md` boot 检查单）
- [x] **T5 `ingest/wiki.py`**（2026-10-29）：`iter_docs` 流式映射 + `hf_hub_download` 下载级幂等 + `run_stage` 处理级幂等；分片 0 = 230,792 行 / 345M 字符；`pytest` 68 绿；W1 终极验收 `SELECT source, COUNT(*) FROM raw_docs GROUP BY source` → `wiki 230792`。交付说明：`docs/ingest-wiki.md`
- [x] **四道口试补答**（#004 两题 + #005 两题）：口头交卷（owner 要求答案不进仓库）
- [ ] **T6**：OSCAR + 新闻适配器（`ingest/oscar.py`、`ingest/news.py`）——等 T5 跑通后再出任务书；`common/textstats.py` 的计数口径直接复用

---

## 四、已关账（不用再想）

- ✅ 骨架 + git（quannie 署名，两 commit）
- ✅ 三份 schema + 四份 configs 落盘
- ✅ ① text_normalization 定稿（v1.0.0 + 元 schema + 折叠范围实测）
- ✅ HF 镜像环境变量
- ✅ D4/E4 理解题
- ✅ ② T2 代码与测试（13 测试）+ ③ 口试三题成稿
- ✅ ④ T1 ids.py（10 测试）+ ⑤ 口试 V1–V4 成稿
- ✅ T3 pii.py（8 测试）、T4 idempotent.py（8 测试）、⑦ 依赖与 key registry
- ✅ T5 wiki 适配器 + W1 终极验收（68 测试绿、230,792 行入 raw 层、外部表 `raw_docs` 可查）
- 验收结论：口试 7/7 通过、47 测试全绿、本批次批准关账（含必修 1/2）

---

## 五、review 备忘（不阻塞，规模化前处理）

- [ ] **metastore 需要 S3A 凭据 + hadoop-aws jar**（T5 验收实测）：缺凭据时 `MSCK REPAIR`、`CREATE DATABASE`（要 mkdir）、"校验已存在的表位置"全挂，Spark 静默降级成 "Spark SQL specific format"——`raw_docs` 现在就是这种表，Spark 能查、Hive/Trino 读不懂。修法：compose 给 metastore 注入 `AWS_ACCESS_KEY_ID/SECRET` + `SERVICE_OPTS` 里的 `-Dfs.s3a.*`，并确认镜像内有 `hadoop-aws`；修好后 `raw_docs` 应改成 Hive 兼容表（见 `docs/dev-lessons.md` #006 坑三）
- [ ] **`first_seen` 台账规模**：23 万条 21.9 MB 一整块 JSON；6 分片（≈140 万条）时换 SQLite 或按 source 分桶，别等它一天扫一遍
- [ ] **上传应进 DAG**：`mc cp` 到 `s3a://raw/docs/source=<source>/` 现在是手工两条命令，Airflow 接上后必须是一个 task（否则"raw 层有没有数据"取决于人记不记得）
- [ ] **raw 表格式为 Spark-specific**：与上面第一条同源；短期可接受（W1 验收只要求一条 SQL），W3 前必须定：修 metastore 还是 raw 层也上 Iceberg（当前决策：raw 永远是 Parquet，不迁移）
- [ ] **schema v0.3.2 的政策修订待 owner 复核**：`policies.content` 从"存规范化 Markdown"改为"格式由 registry 的 `content_format` 声明"——这是 wiki 实测（plain_text）逼出来的，但动了 schema 政策文本，需要一句确认
- [ ] `changelog.append_change(dedupe=True)` 每次全文件扫描，O(n)；规模化后换内存索引（或按 doc_id 分桶）
- [ ] `BANK_CARD_RE` 边界只挡数字不挡字母，`AB123456789012345678CD` 这类字母包裹串有概率碰巧过 Luhn；补 `(?<![0-9A-Za-z])` 前后断言
- [ ] `runs.finish` 每次全量读日志，append-only 的已知代价；规模化换 SQLite 或内存索引
- [ ] `now_iso` 在 `runs.py` 与 `changelog.py` 各写一份，该收进 `common/__init__.py`（或 `common/timeutil.py`）

### compose review 小项（已处理）

- [x] Kafka 发布了 9092 但广播 `kafka:9092`，宿主机客户端解析不到 → 判定**消费方都在网内**，删掉端口发布并注释说明；将来要宿主消费再加 `PLAINTEXT_HOST` listener
- [x] 注释「只发布三个端口」与实际不符 → 改为逐个列出五个端口及用途
- [x] `bitnami/kafka:3.7` 浮动 tag → pin `3.7.1`
- [ ] 镜像 tag 存在性未验证（离线环境，Docker Hub 不可达）：`bitnami/kafka:3.7.1`、`apache/hive:4.0.0` 均需 boot 时 `docker pull` 确认

---

## 六、本周执行建议

| 时间块 | 动作 | 出口检查 |
|--------|------|----------|
| 第 1 个半天 | ① 六条 owner_todo 全部写成"决定 + 理由"，升 version | config 里不再有 `owner_todo: true` |
| 第 2–3 个半天 | ② 落盘 + 改掉 6 条 review 意见 + 5 类测试 | 本地 `pytest -v` 全绿 |
| 第 3–4 个半天 | ④ `ids.py` 读 config + 5 条验收测试 | T1 测试全绿 |
| 第 4 个半天 | ③ + ⑤ 七个口试题写答案（各 ≤3 句） | 每题都有结论 + 依据 |
| 穿插 | ⑥ 全程挂着，卡一天即切 plan B；⑦ 十分钟清掉 | `huggingface-cli` 验证输出已贴 |

**交付纪律**：一次提交只覆盖一条链（B 链的 commit 不含 T1 代码），commit message 带 `W1/W2` 标签；口试题答案贴在 commit 正文或 `docs/` 下，不留在聊天记录里。
