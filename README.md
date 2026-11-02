# 濯莲 Lotus

**可编程的数据质量控制器——数据清洗不再是写死的脚本，而是你手里的一排旋钮。**

传统数据清洗是写死的规则：想改一个标准要动代码，效果好不好全凭感觉，换一批数据就得重写一遍。

濯莲把这件事变成一台控制台：

- **看得见** —— 数据质量是一排仪表盘，不是一句「感觉更干净了」
- **拧得动** —— 每个清洗决策都是一个旋钮，换标准不换代码
- **换得起** —— 干净优先、多样优先、去重优先，治理配方一键切换
- **查得回** —— 每条数据的每次去留都有账本，每个结果都能一键复现

**脏的进来，纯的出去，旋钮在你手里。**

> 英文一句话：**Lotus — a programmable data quality controller. Clean data isn't written, it's tuned.**

---

## 这台控制台现在能做什么

七层治理链路已在真实数据上端到端跑通：**多源采集 → 清洗 → 去重 → 质量门禁 → 配比采样 → 版本化发布 → 下游验证**。每个数字可归因、每次运行可复现、每个阈值可配置。

### 用数字说话

| 层 | 结果 |
|---|---|
| 采集 | 231,314 篇真实多源中文文档 |
| 清洗 | 删 6.1%（样板 8,609 / 非目标语言 3,771 / 过短 1,900），PII 真脱敏 82 条 |
| 去重 | 精确 + 近重复删 0.52%，全部留有「谁压掉谁、相似度多少」的账 |
| 质量门禁 | 五维打分，放行 88.5% |
| 配比发布 | 2M token 预算分层采样，发布 v0.1（license 执法剔除不合规源） |
| 下游验证 | 四臂困惑度消融：clean 较 raw **−4.3%**（带置信区间）；并发现配比采样的分布代价 |
| 工程底座 | 7 容器基础设施 + 125 项测试（含变异测试/合同测试）+ 9 个真实踩坑实录 |

### 路线图（控制台的四块仪表）

```
✅ 审计闭环     每条数据去留可归因到 run，产物逐文件验货
✅ 旋钮全量开放 全部阈值/规则/配比在配置层，改标准不动代码
🚧 指标卡        治理前后全指标对比卡（重复率/多样性/信息密度…）——建设中
⬜ 配方引擎      干净/多样/去重三档配方一键切换 + trade-off 护栏
⬜ 旋钮自动搜索  指标目标 → 清洗参数的带约束闭环
⬜ 因果链终证    v0/v1 各微调一个小模型，验证训练收益
```

## 快速上手

前置：Windows + Docker Desktop + Python 3.11+。

```bash
git clone https://github.com/quannie255-star/-Lotus.git && cd -Lotus
pip install -r requirements.txt

setx HF_ENDPOINT "https://hf-mirror.com"     # 新开终端生效；huggingface-cli login

cp .env.example .env                          # 改两个 replace_me
bash docker/metastore/README.md 中的三条 curl # metastore 外挂 jar（一次性）

docker compose up -d --build                  # 七容器栈（镜像源方案见 dev-lessons #004）
python -m ingest.wiki 1                       # 首个源：23 万篇真实维基文档
python smoke/m1.py                            # 一行冒烟：PASS/FAIL
```

## 文档地图

| 想了解 | 去哪 |
|---|---|
| 怎么改任何参数/规则/阈值 | `MANUAL.md`（反查表 + 微调点索引） |
| 架构与设计决策 | `ARCHITECTURE.md`、`docs/blueprint.md`、`docs/wp-01-stage-protocol.md` |
| 开发过程与踩坑实录 | `DEVELOPMENT_LOG.md`、`docs/dev-lessons.md`（9 个真实坑） |
| 阶段契约与项目宪法 | `docs/wp-01-stage-protocol.md` §0 七条不变量 |

## 项目宪法（七条不变量）

原始数据不可变 · 一表一阶段 · 确定性身份 · 字节级幂等 · 变更可归因 · 事实单一来源 · 坏数据不静默。任何偏离都是 bug，豁免必须显式记录（见 `docs/wp-01-stage-protocol.md`）。

---

License: MIT · 作者 quannie · 项目前期代号 datalens
