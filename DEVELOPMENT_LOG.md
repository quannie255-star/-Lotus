# 开发日志（DEVELOPMENT_LOG）

---

## Tier 0 — 指标卡 + GitHub 上线（2026-10-31，Lotus 更名日）

**目标**：新定位「可编程数据质量控制器」的第一块仪表——治理前后全指标对比卡；
项目更名 datalens → Lotus（濯莲）并推上 GitHub。

### 实际做了什么

- `metrics/card.py`：`compute_card`（确定性蓄水池抽样 ≤20k 行；精确重复率=规范化哈希
  seen 集合 O(n)；完整性/语言一致性/文档内冗余/信息密度代理；语义多样性 v0=None 空读数
  不假装）+ `compare_cards`（方向语义标注：重复率降=改善，完整性升=改善）；
  `metrics/run.py`：v0(raw) 与 v1(release) 各拍一张卡 + 对比卡（阶段幂等）；
- 看板新增「治理对比」首Tab；README 按控制器定位重写 + 路线图 + 数字成绩单；
- 全仓 datalens → Lotus 对齐（历史文档保留代号注记）；
- **GitHub 历史清洗**：M4 提交时 data/clean/source=wiki/part-00000.parquet（573MB）漏进
  历史（当时 .gitignore 未覆盖 data/clean/）→ GitHub 拒收（GH001）→ filter-branch 全历史
  移除 + refs/original 清理 + gc --aggressive：.git 从 506MB → **398KB** → 推送成功。

### 关键决策

- **T0-D1 抽样口径显式化**：指标卡内容指标基于 ≤20k 确定性抽样（seed=2026），卡片里
  记 sampled_rows，不冒充全量——23 万行逐条规范化是分钟级×N 的代价，读数不需要；
- **T0-D2 空读数纪律**：语义多样性未接入就是 null+备注，绝不给假数（控制台的公信力
  高于指标的完备度）；
- **T0-D3 大文件历史清洗而非 LFS**：产物是可重算的，不配 LFS；规则「data/ 产物永不入库」
  由 .gitignore 全 zone 覆盖兜底。

### 实跑（真实数据）

```
$ python -m metrics.run   →  v0-raw → v1-release 全指标对比：
  format_completeness  0.7257 → 1.0000  Δ+0.2743 [改善]
  language_consistency 0.6882 → 0.7567  Δ+0.0685 [改善]
  info_density_proxy   0.9966 → 0.9988  Δ+0.0022 [改善]
  exact_dup_rate       0.00025 → 0.0    Δ−0.00025[改善]
  internal_redundancy  0.0034 → 0.0012  Δ−0.0022 [改善]
  n_docs               231314 → 2402（配比采样，规模列）
$ python -m pytest -q → 129 passed
$ git push → github.com/quannie255-star/-Lotus main 上线（20+ commit 完整开发史）
```

### 遗留

1. dirty_reject_rate 在 release 卡为 None（changelog 口径只对 raw 端有意义）——对比卡
   该行暂缺，Tier 1 时按 run 归属修正；
2. 语义多样性/正式信息密度定义（Tier 1/2）；
3. N1 OSCAR 实测、tiny 模型消融、配方引擎——路线图 ⬜ 项。

---


> 模板：日期 / 里程碑 / 目标 / 实际做了什么 / 关键决策及理由 / 遇到的问题及解决 /
> 怎么验证的 / 微调入口 / 遗留问题。决策编号 M{里程碑}-D{n} 会被代码内联注释引用。

---

## M5-M10 — 主链全模块（2026-10-30，代码全绿；真实链路跑通于 dedup 完成后补录）

**⚠️ 本条目尾部「真实链路终录」补记了 10-31 的三次实战修复与最终六层数字——先读它。**

**状态**：六个里程碑代码+测试一次交付（commit f3fd783，124 测试全绿）；真实数据链路
（quality→mix→release→downstream）阻塞在 dedup 真实跑（216k 行纯 Python MinHash，
E2 素材：单机纯 py 的耗时基线）。本条目按里程碑分小节，真实数字补录于各节「实跑」。

### 真实链路终录（2026-10-31，M6-M8 实跑关账）

三次实战修复（全部被真实数据打出来的，不是 review 出来的）：

1. **completeness 误杀（M6 真实数据教训）**：首版把维基的节标题行（「题解」「评价」）
   当段落统计，27.4% 文档低于 0.3 → 门禁误杀 78%（16.8 万条记录卡爆记账）。修复：
   只统计**句式行**（≥15 字符）——截断文的特征是长段无标点结尾，结构行不是证据。
   回归测试 `test_completeness_ignores_header_lines`。修复后放行 88.5%（删 11.5%）。
2. **latest_outputs 双读（框架级）**：共享 manifest 含 clean+dedup 两键，quality 无过滤
   读了 43.2 万行（每文档算两遍）。修复：`latest_outputs(stage_dir, only="dedup")`
   阶段过滤参数（API 级，quality/mix/release 三处同修）。
3. **changelog 提交 O(n²)（M4 性能债爆雷）**：逐条 append_change(dedupe) 每条全文件
   扫描，5 万条预计 1 小时+。修复：批量单趟提交（一次读全局建键集合，一次写新增），
   实测秒级。恢复流程顺带验证了 dedupe 键对中断重试的吸收能力。

**六层漏斗（终数）**：
```
raw 231,314 → clean 216,738 (−6.1%) → dedup 215,611 (−0.52%)
→ quality 190,851 (放行 88.5%, 删 24,760) → mix 2,403 (2M token 预算)
→ release 2,402 (v0.1; 1 行 unknown-license 被执法剔除)
```

**四臂 ppl（唯一核心指标首跑，char-5gram toy LM / 1.5M chars/臂 / 600 行冻结评测集）**：
```
raw      3277.65  [3007.2, 3535.2]
clean    3136.89  [2879.5, 3324.3]   ← Δ(raw) = −140.8 (−4.3%)，方向正确
dedup    3117.05  [2879.9, 3247.0]   ← 再降 19.8
release  3396.77  [3172.0, 3596.5]   ← 反升：真发现，见下
```

**release 反升的归因（协议设计想要的发现，不是事故）**：mix 按质量分降序取头部，
release 臂的训练语料分布比评测集「更窄更纯」——toy LM 上表现为分布偏移惩罚。
两个改进方向已记 backlog：评测集从「中性 held-out」抽（而非 clean 层）；或各臂
按同分布同规模重采样。**这个发现本身就是面试素材：指标会惩罚「数据变好但分布变窄」，
单向度的"ppl 降=好"是幼稚读法。**

**审计债（诚实记录）**：全局 changelog 里残留 ~1.1 万条来自被杀掉的坏 run 的
quality 记录（那些文档现已放行）——dedupe 键吸收了重放但没机制作废旧记录。
修法（作废条目/按 run_id 对账视图）记遗留，W6 异常追踪时一并做。



### M5 — dedup（exact + MinHash + 跨源矩阵）
- `dedup/engine.py`：`shingles/minhash_signature/UnionFind/dedup_pass`（两级：规范化哈希
  精确 → LSH 候选+真实 Jaccard 复核+并查集成簇；canonical=源信任→内容长→doc_id）；
  `dedup/run.py`：阶段入口（签名表/报告/changelog 三产物）；`configs/dedup_policy.json`。
- 教训：keep/kept 变量名手滑导致全量误删（首测逮住）；JSON 不认 Python 下划线数字。
- 实跑（2026-10-30 22:47 落地，含纯 py MinHash 全程约 75 分钟——E2 的单机基线）：
  ```
  input 216738 → kept 215611（删 1127 = 0.52%）
  exact: 88 条（64 组）｜ near: 1039 条（wiki 894 / oscar 116 / news 29）
  跨源矩阵：空（mock 语料本无跨源重复——真 OSCAR/news 接入后才有意义，N1/N2）
  ```
- 报告口径 bug（遗留）：`near_clusters` 恒 0——簇字典键是并查集根 doc_id，
  `startswith("near:")` 判断错对象；删除数与 changelog 账目已对平（1127=1127 ✓）。

### M6 — quality（五维 + 门禁 + 污染）
- `quality/engine.py`：五维纯函数（completeness=段尾标点率 / uniqueness=1−最近邻 /
  consistency=FFFD 密度 / non_pollution=n-gram 命中 / length=体裁带）+ `gate` 三分 +
  `build_ngram_index`；`quality/run.py`：阶段入口（benchmark 缺失=显式 skipped 不假检测）；
  `configs/quality_policy.json`（权重+阈值+隔离带）。
- 变异测试（纪律 2）：截断文/超短文必须被拦，好文必须放行——`test_gate_variants_mutation_matrix`。
- judge/蒸馏按砍单序 2 未实装（policy 有 status 记录）。

### M7 — mix + release
- `mix/run.py`：`token_count_estimate`（char-estimate-v1，Qwen 升级位）+ `stratified_sample`
  （层=source，质量降序贪心取满 token 目标，允许超额；枯竭单轮再分配；全程无随机数）；
- `release/run.py`：`next_version`（semver 三档）+ license 执法（unknown/restricted 剔除
  并记 changelog）+ `build_provenance_card`（DPI 风格 YAML，逐源 license 表+删除统计）。

### M8 — downstream ppl（唯一核心指标）
- `downstream/ppl.py`：`CharNgramLM`（5-gram add-k，纯 Python——N7 决策：kenlm 需 MSVC）+
  `bootstrap_ci`（重采样 95% CI）；`downstream/run.py`：四臂协议（raw/clean/dedup/release
  各训一个 LM，同一冻结评测集比 ppl——防「不同分布直接比」的经典混淆）。
- 文档化性质：LM 计数可加性仅在切点对齐时精确成立（任意切点丢边界 n-1 gram）。

### M9 — stream + DAG
- `stream/producer.py`：确定性模拟行（doc_id 确定 → 重放被 dedup 折叠——流式幂等的身份解）
  → kafka-console-producer 通道（零客户端依赖）+ `consume_peek` 验证；
- `dags/corpus_pipeline.py`：四源扇入→主链的 Airflow DAG（retries=2 指数退避，与 run_stage
  幂等语义咬合）。⚠️ 激活需 airflow 容器挂仓库+装依赖（compose 未配，遗留）。

### M10 — dashboard
- `dashboard/app.py`：Streamlit+DuckDB 四视图（漏斗/删除归因/质量分布/runs 审计），
  只读旁路；真实数据冒烟通过（漏斗 231,314→216,738；归因 top3=boilerplate 8609/
  lang 3771/too_short 1900；runs 18 条）。

### 微调入口（M5-M10 新增，均已入 MANUAL 反查表）
dedup 参数（dedup_policy）/ 质量权重与门禁（quality_policy）/ 配比与预算（mix_policy）/
发布 license 政策（release/run.py RELEASE_POLICY）/ ppl 训练量（downstream/run.py 常量）。

### 遗留问题（M5-M10 合并）
1. dedup 真实跑耗时（纯 py 单机基线，补录后即为 E2 对照点；Spark/Ray 版是 W4/W5 正戏）；
2. Airflow DAG 激活（容器挂载+依赖）；Spark Structured Streaming 消费作业（kafka 包）；
3. benchmark 下载（ceval/CMMLU）接污染索引；4. judge 蒸馏（砍单序 2）；
5. release 的 MinIO 发布（当前本地目录，mc cp 上桶为发布仪式）；6. changelog 重算净增 4 条之谜。

---

## M4 — clean 框架 + WP-01 六件套（2026-10-30）

**目标**：clean 阶段真数据跑通；配置进指纹；changelog 两段式；合同测试。

### 实际做了什么

- `common/stage.py`（WP-01 六件套实装）：`configs_digest` / `snapshot_configs`（配置快照=指纹锚+
  provenance 双用途）/ `latest_outputs`（上游验货：manifest∧逐文件哈希）/ `StagedChangeLog`
  （part 暂存→成功 commit（dedupe 吸收重试）/失败 discard）/ `run_pipeline` 薄驱动；
- `clean/base.py`：规则框架（判定协议 keep/drop/transform+meta；规则=配置装配的纯函数；
  变更走 StagedChangeLog；stats 侧信道=看板数据源）；
- `clean/rules.py` 六规则：too_short / lang_ratio（v1=Han 占比确定性检测器，N4 fasttext 位）/
  mojibake（U+FFFD 密度）/ boilerplate（链接密度+短行占比）/ pii_redact（掩码保长，meta 带
  old/new hash+pii_types）/ url_norm（canonical_url 回填，M4-D3 显式豁免不记 changelog）；
- `clean/run.py`：家族入口——latest_outputs 验货四源 raw → 规则链 → 按源写
  `data/clean/source={s}/part-00000.parquet` → run_stage 幂等；
- `configs/clean_policy.json`（全部阈值+note）；`tests/test_clean.py` 16 测试
  （六件套单测 + 规则族边界 + 合同 T1-T4）。

### 关键决策

- **M4-D1 配置进指纹**：snapshot 并入 input_paths——修掉 M1 遗留「改配置不重算」；T3 合同测试锁定；
- **M4-D2 规则是数据**：加规则=clean_policy 加一条+纯函数，无代码分支改动；
- **M4-D3 url_norm 豁免记录**：canonical_url 是派生字段回填非内容变更，不进 changelog
  （否则全量行都有一条 transform 记录，账本被噪音淹没）；redact 必记（内容变了）；
- **M4-D4 产物哈希键改相对路径**（框架级修复，见下）。

### 遇到的问题及解决（今天框架级 bug 密度最高）

1. **产物哈希扁平键碰撞**（合同 T1 逮住）：output_hashes 按文件名做键，clean 的
   `source=wiki/part-00000.parquet` 与 `source=oscar/part-00000.parquet` 同名碰撞且哈希路径
   错位 → 永不命中缓存。修复：键=相对 work_dir 路径（M4-D4，扁平产物向后兼容）；
2. **测试状态泄漏**（单跑过、全套挂）：StagedChangeLog 全局默认路径指向仓库真实 changelog，
   与并发真实运行互踩计数。修复：fixture 隔离 monkeypatch CHANGELOG_PATH——「测试必须连账本一起隔离」；
3. **run_id 占位污染**：记录带 "pending" run_id，commit 的 setdefault 不覆盖 → 永久占位身份。
   修复：StagedChangeLog 构造于 transform 内（此刻才有真 run_id），行内零占位；
4. **掩码保长语义**：断言 after<before 错——掩码策略刻意保长（3+4 可见+星号），正确断言=
   长度不变、hash 必变；
5. 测试算术错误两处（「短」行被 boilerplate 补刀：单行短文短行占比 100%）——期望值修正；
6. **性能画像**：230k 行/600MB 真实数据全程 ~11 分钟，瓶颈=每记录一次 jsonschema 校验 +
  每记录一次文件开关（14k 条变更记录）。缓冲句柄+批量校验列 backlog（OSCAR 规模前必修）。

### 怎么验证的（实跑）

```
$ python -m pytest -q
104 passed in 8.01s               （88 → 104）

$ python -m clean.run（首跑，旧代码进程/新 manifest 差异见问题 1）
{"cached": false, "rows": 216738, "changelog_added": 14362, sources: [news,oscar,pdf,wiki]}

$ python -m clean.run（三跑）
{"cached": true, "rows": null}    ← 幂等闭环（二跑重算系旧 manifest 键制，三起新键即中）

$ 账目对平：wiki 230792 − 14280 删 = 216512 + oscar 195 + news 30 + pdf 1 = 216738 ✓
$ 真实删除归因（changelog 聚合）：
  wiki boilerplate_heavy 8609 | lang_mismatch 3771 | too_short 1900 | pii_redacted 82
  （项目首批真实治理数字：删除率 6.1%，PII 命中 82 条）
```

### 微调入口

MANUAL 新增：清洗规则阈值（clean_policy.json 各节）、规则顺序（rules_order）、
语言检测阈值（N4 升级位注释）。

### 遗留问题

1. clean 性能（见问题 6）；2. 重算时 changelog 净增 4 条（dedupe 键未覆盖的边角，待查）；
3. 存量 docstring 四段标准 retrofit 仍未做（ids/runs/changelog/first_seen）；
4. wiki 82 条 PII 样本值得人工抽检（误报率估计）。

---

## M3 — news + pdf 源（2026-10-30）

**日期**：2026-10-30。**里程碑**：M3。
**目标**：四源集齐（oscar mock / wiki 真 / news mock-jsonl / pdf 真链路生成件），parse 层落地。

### 实际做了什么

- `parse/pdf_extractor.py`：`extract_pdf -> ExtractResult`（PyMuPDF 文本层轻路径；疑似扫描页
  → warnings 不硬造内容；坏文件异常捕获进 warnings 走 quarantine）；
- `ingest/pdf.py`：`load_entries / fetch_bytes(%PDF 魔数校验) / archive_raw(按 sha256
  内容寻址归档原始字节=raw 层不可变) / build_row / run(文件级聚合护栏)`；显式 entries
  落盘 pdf.entries.json 作指纹锚（防假缓存）；
- `ingest/news.py`：本地 jsonl 契约完整适配器（干净源 fail-fast；publisher/section 进
  source_metadata；published → source_timestamp）+ mock 生成器 + main 入口；
- `ingest/spine_schema.py`：source_metadata struct 扩为 {page_title, publisher, section}；
  key registry 登记 extract_engine/pdf_sha256、mineru_version 标 deprecated；
- requirements 钉 pymupdf==1.28.2、requests==2.32.3。

### 关键决策及理由

- **M3-D1 PDF 分层提取**：文本层走 PyMuPDF 秒级；扫描件只警告不硬造（OCR=成本悬崖）；
  registry content_format 同步 plain_text——声明必须与事实一致。
- **M3-D2 原始字节内容寻址归档**：{sha256}.pdf 归档件即 raw 证据；同文件重复登记天然去重；
  source_id=sha256(bytes)（无稳定主键源，registry 声明）。
- **M3-D3 metadata 容器键并集**（struct 加可空键而非 map）：兼容既有 wiki 数据；
  代价=每加键动 schema，由 key registry 元测试强制登记。
- **M3-D4 news 干净源策略**：缺 id/空 content fail-fast，与 oscar quarantine 构成 M2-D4 两极。

### 遇到的问题及解决

1. `insert_text` 不折行 → CJK 长句出页宽被裁（每页 12 字）→ 误报扫描页；改
   `insert_textbox` + 阈值 20→10（19 字短目录页不该误伤）；
2. `raw_uri` 的 `relative_to(ROOT)` 在注入 tmp 归档目录时 ValueError → 仓库内相对/外绝对；
3. **entries 指纹锚陷阱**（自查逮住）：固定文件当指纹 = 不同清单假缓存 → entries 落盘
   为字节稳定文件再作输入 + 回归测试 `test_pdf_entries_fingerprint_isolation`；
4. news 漏 main() → `python -m` 静默空跑；补齐并记入遗留（家族 CLI 存在性检查 → M4）；
5. 元测试逮人 ×2：requirements 未钉版、key registry 漏登记——守卫测试让机器替人守纪律。

### 怎么验证的（实跑）

```
$ python -m pytest -q
88 passed in 5.18s                        （80 → 88）
$ python -c "from ingest import news; print(news.run()['rows'])"
30
$ python -m ingest.news（二次）→ "cached": true          ← 幂等
$ pdf 生成件实跑 → rows=1, 归档件落位, raw_uri 可解析
```

### 微调入口

MANUAL 新增行：PDF 源清单 / 扫描页阈值 / PDF 护栏 / news 输入文件（见反查表 M3 段）。

### 遗留问题

1. pdf_sources.json 真实政府 URL 待网络窗口录入；2. N1/N2 仍开；
3. 家族 CLI 存在性检查 → M4 合同测试；4. MinerU 扫描件路径（N3）。

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

## M1 — 骨架 + wiki 全链路（2026-10-26 ~ 10-30）

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
