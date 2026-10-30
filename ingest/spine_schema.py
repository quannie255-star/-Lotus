"""ingest/spine_schema.py — 所有源适配器共用的产物 Parquet schema（DEVLOG M2-D1）

为什么从 wiki.py 抽出来：第二个适配器（oscar）出现，spine 产物 schema 从"某模块的
私有常量"升级为"家族契约"。各源未检测的字段不出现（三分法见 ingest/wiki.py 模块头），
顺序 = schema/unified_schema.json 的 spine 顺序。

幂等: 纯常量；改它 = 改全家族的产物 schema，必须递增 schema_version 并全链路 force 重算。
"""

import pyarrow as pa

SPINE_PARQUET_SCHEMA = pa.schema(
    [
        pa.field("doc_id", pa.string(), nullable=False),
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("url", pa.string()),
        pa.field("canonical_url", pa.string()),
        pa.field("content_hash", pa.string(), nullable=False),
        pa.field("content", pa.string(), nullable=False),
        pa.field("content_format", pa.string(), nullable=False),
        pa.field("raw_uri", pa.string()),
        pa.field("original_encoding", pa.string()),
        pa.field("language", pa.string(), nullable=False),
        pa.field("char_count", pa.int64(), nullable=False),
        pa.field("cjk_char_count", pa.int64(), nullable=False),
        pa.field("utf8_byte_count", pa.int64(), nullable=False),
        pa.field("source_timestamp", pa.string()),
        pa.field("timestamp_source", pa.string(), nullable=False),
        pa.field("first_seen_at", pa.string(), nullable=False),
        pa.field("genre", pa.string(), nullable=False),
        pa.field("license", pa.string(), nullable=False),
        pa.field("license_status", pa.string(), nullable=False),
        pa.field("commercial_use", pa.string(), nullable=False),
        # 源特化收敛容器：键 = 各源登记键的并集（wiki:page_title / news:publisher,section）。
        # 新源要新键 = 改这里 + key registry 登记 + schema_version 递增（DEVLOG M3-D3）
        pa.field("source_metadata", pa.struct([
            pa.field("page_title", pa.string()),
            pa.field("publisher", pa.string()),
            pa.field("section", pa.string()),
        ])),
    ]
)
