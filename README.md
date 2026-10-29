# datalens — 中文大模型数据生产 Pipeline

采集 → 解析 → 清洗 → 去重 → 质量 → 配比 → 发布的全链路语料生产管道，
Spark/MinIO/Hive Metastore/Airflow/Kafka 容器化底座，全程幂等、可审计、可复跑。

**当前进度**：M1（骨架 + wiki 源全链路）✅。里程碑路线与设计见 `docs/blueprint.md`，
阶段契约见 `docs/wp-01-stage-protocol.md`，踩坑记录见 `docs/dev-lessons.md`。

---

## 快速上手：新机器从零到跑通 M1

前置条件：Windows + Docker Desktop + Python 3.11+ + git。以下命令逐条可复制（Git Bash）。

```bash
# 0) 克隆并进入
git clone <repo-url> datalens && cd datalens

# 1) Python 依赖
pip install -r requirements.txt

# 2) 密钥与镜像（HF 国内镜像 + 登录）
setx HF_ENDPOINT "https://hf-mirror.com"        # 新开终端生效
huggingface-cli login                           # 粘贴 HF Access Token
#    gated 数据集（OSCAR）还需网页登录同意条款一次

# 3) 容器凭据
cp .env.example .env                            # 改掉两个 replace_me

# 4) metastore 外挂 jar（不进 git，约 311MB，一次性）
bash docker/metastore/README.md 里列出的三条 curl   # 详见该文件

# 5) 启动容器栈（镜像若拉不动：镜像源方案见 docs/dev-lessons.md #004）
docker compose up -d --build                    # 首次含 Spark 镜像构建（下载 s3a jar）

# 6) 拉取 wiki 源并落 raw 层（约 30 万行，单分片 ~490MB 下载）
python -m ingest.wiki 1

# 7) 上传到 MinIO raw 桶（hive 分区目录）
docker compose exec minio mc alias set q http://localhost:9000 \
  $(grep MINIO_ROOT_USER .env | cut -d= -f2) $(grep MINIO_ROOT_PASSWORD .env | cut -d= -f2)
docker compose exec minio mc cp data/raw/wiki/ingest/part-00000.parquet q/raw/docs/source=wiki/

# 8) 一行冒烟：五步检查（栈/单测/本地产物/MinIO/验收 SQL），输出 PASS/FAIL
python smoke/m1.py
```

预期末行输出：

```
SMOKE M1: PASS ✅ (5/5)
```

## 日常命令速查

```bash
python -m pytest -q          # 全部单测（68 个，<3s）
python smoke/m1.py           # 里程碑冒烟
docker compose ps            # 服务状态
python -m ingest.wiki        # 重跑 ingest（命中缓存零计算；force 见 MANUAL）
```

## 目录速览

```
common/     身份(ids) 审计(runs,changelog) 幂等(idempotent) 台账(first_seen) 计数(textstats) 日志(log)
ingest/     源适配器（wiki ✅；oscar/news/pdf 见路线图）
clean/      清洗（pii ✅；规则框架 M4）
schema/     unified / removal_log / dedup_signature 三份契约
configs/    source_registry / reason_registry / text_normalization / url_canonicalization
sql/        外部表 DDL + 验收查询
smoke/      里程碑冒烟脚本
docs/       blueprint / wp-01 阶段契约 / dev-lessons 踩坑录
docker/     metastore jar 说明、spark Dockerfile
```

操作手册（怎么改东西）→ `MANUAL.md`；架构与决策 → `ARCHITECTURE.md`；
开发史 → `DEVELOPMENT_LOG.md`。
