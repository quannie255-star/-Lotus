-- sql/raw_docs.sql — raw 层外部表 + W1 终极验收 SQL（T5）
--
-- 用法（在 repo 根目录）：
--   docker compose cp sql/raw_docs.sql spark-master:/tmp/raw_docs.sql
--   docker compose exec -T spark-master /opt/spark/bin/spark-sql \
--       --conf spark.sql.hive.manageFilesourcePartitions=false -f /tmp/raw_docs.sql
--
-- 三个必须知道的约束，全部是这次实测撞出来的（不是推测）：
--
-- 1. LOCATION 不能是桶根：Hive 4 校验合法表位置时报
--    `InvalidObjectException: raw_docs location must not be root path`，
--    `s3a://raw/` 一律被拒。Spark 会降级成"Spark SQL specific format"把表塞进 metastore
--    （查询能跑，但别的引擎读不懂这张表的 schema）。所以数据放在**多一级前缀**下：
--        s3a://raw/docs/source=wiki/part-00000.parquet
--        LOCATION 's3a://raw/docs/'
--    `source=...` 仍是目录名，分区裁剪照样成立；多这一级换的是"Hive/Trino 也能读"。
--
-- 2. PARTITIONED BY (source) 时，表定义里**不能**再声明 source 列（分区列与文件列同名，
--    Spark 报 duplicate column）。文件里其实有 source 列（spine 字段），Spark 按名字读、
--    只取表里声明的列，多余列自然忽略；对外的 source 一律来自目录名 → 单一事实来源。
--
-- 3. --conf spark.sql.hive.manageFilesourcePartitions=false：分区交给文件系统发现，
--    不向 metastore 注册。原因是向 metastore 注册分区会让 metastore 自己去校验 s3a 路径，
--    而 metastore 容器没有 S3A 凭据（`NoAuthWithAWSException`，dev-lessons #005 坑五同源）。
--    对 append-only 的 raw 层，文件系统发现本来就更合适：mc cp 上传完就能查，
--    不需要 MSCK REPAIR。curated 层（W7 Iceberg）走另一套元数据。
--
-- 4. 用 default 库，不新建 corpus 库：CREATE DATABASE 会让 metastore 去 s3a 上 mkdir，
--    同样因为没有 S3A 凭据而 MetaException（#005 坑五同源）。

DROP TABLE IF EXISTS raw_docs;

CREATE EXTERNAL TABLE raw_docs (
  doc_id            string,
  schema_version    string,
  source_id         string,
  url               string,
  canonical_url     string,
  content_hash      string,
  content           string,
  content_format    string,
  raw_uri           string,
  original_encoding string,
  language          string,
  char_count        bigint,
  cjk_char_count    bigint,
  utf8_byte_count   bigint,
  source_timestamp  string,
  timestamp_source  string,
  first_seen_at     string,
  genre             string,
  license           string,
  license_status    string,
  commercial_use    string,
  source_metadata   struct<page_title: string>
)
USING parquet
PARTITIONED BY (source string)
LOCATION 's3a://raw/docs/';

-- W1 验收：source 列自动从目录名长出来，不写一行转换代码
SELECT source, COUNT(*) AS n FROM raw_docs GROUP BY source;

-- 分区裁剪的证据：计划里应出现 PartitionFilters: source = wiki
EXPLAIN SELECT COUNT(*) FROM raw_docs WHERE source = 'wiki';
