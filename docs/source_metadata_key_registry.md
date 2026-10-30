# source_metadata_key_registry

`unified_schema.json` 的 `source_metadata` 是"源特有字段的收敛容器"，它允许任意 key，
但每个 key 及其类型**必须先在本文档登记**，否则下游无法解释、看板无法出图。

规则：

1. 新增 key 先改本文档，再改 `configs/source_registry.json` 的 `metadata_schema`，两者必须一致。
2. key 用 snake_case；跨源含义相同的字段复用同名（如 `publisher`），不要造 `news_publisher`。
3. 值类型只允许：string / number / boolean / array / object（与 schema 的 `source_metadata_value` 对齐）。
4. 不再使用的 key 标 `deprecated`，不要直接删（历史数据里还有）。

## 已登记字段

| source | key | 类型 | 必填 | 说明 | 来源 |
|---|---|---|---|---|---|
| oscar | `warc_record_id` | string | 是 | WARC 记录 ID，OSCAR 的稳定主键 | 源系统 |
| oscar | `crawl_metadata` | object | 否 | 抓取元数据原始结构（时间、UA、状态码） | 源系统 |
| oscar | `content_type` | string | 否 | 原始 MIME 类型 | 源系统 |
| wiki | `page_title` | string | 是 | 页面标题（与正文首行可能重复，保真用途） | 源系统 |
| wiki | `revision_id` | string | — | **deprecated（v0.3.0）**：HF 镜像 `wikimedia/wikipedia` 不含修订号，改用页面 ID 作 `source_id` | 源系统 |
| wiki | `namespace` | string | — | **deprecated（v0.3.0）**：同上，镜像未导出命名空间；条目名空间已由 dump 预处理过滤 | 源系统 |
| news | `publisher` | string | 否 | 发布媒体名；跨源复用 | 源系统 / meta |
| news | `section` | string | 否 | 频道/栏目（要闻、财经…） | 源系统 |
| pdf | `extract_engine` | string | 是 | 提取引擎标识（v1= pymupdf 文本层；MinerU 升级后变更，解析结果必须能追版本） | 解析器 |
| pdf | `page_count` | number | 否 | 原始页数；质量校准用 | 解析器 |
| pdf | `pdf_sha256` | string | 是 | 原始文件字节哈希（= source_id，冗余存储便于 quarantine 表对账） | 派生 |
| pdf | `mineru_version` | string | — | **deprecated（M3）**：v1 用 PyMuPDF 文本层，无 MinerU；升级后由 `extract_engine` 承载 | 解析器 |

## 待登记（占位，等实际接入时补）

| source | key | 类型 | 说明 |
|---|---|---|---|
| — | — | — | — |
