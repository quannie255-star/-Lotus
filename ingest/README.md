# ingest

职责：把**四种结构完全不同的源**（wiki / oscar / news / pdf）映射成同一种 spine 字典流，
交给下游（clean → dedup → quality → mix → release）。**只映射，不判断。**

## 适配器契约（唯一的接口）

```python
SOURCE = "wiki"

def download(n_shards: int = 1) -> list[Path]:      # 下载级幂等：缓存 + 断点续传
def iter_docs(parquet_path: Path) -> Iterator[dict]:  # 处理级映射：逐条 yield spine 字典
def run(n_shards: int = 1) -> dict:                  # run_stage("ingest", ...) 包起来
```

三条纪律，每条都对应一个已有资产：

| 纪律 | 为什么 | 落点 |
|---|---|---|
| 流式，不整读 | 单分片 23 万行、四个源将来上百万行，内存里只该有一条 | `ParquetFile.iter_batches` + `yield` |
| 只映射，不清洗 | raw 层完整性：一条不删，clean 才有得删、有得记 changelog（**ingest 零 changelog**） | `map_row` 只做取值/派生 |
| 身份指纹走 `common.ids`，许可体裁走 `source_registry` | 适配器不许发明事实；硬编码 = 接缝漏了 | `spine_doc_id` / `content_hash`；`registry()` |

## 两级幂等（现成积木拼接，不重复造轮子）

```
hf_hub_download  （下载级：本地缓存 + 断点续传，免费）
      ↓
run_stage("ingest", ...)  （处理级：输入指纹 = 已下载的分片文件）
```

T4 的 fingerprint 输入就是"分片文件"，所以下载级和处理级自动串起来：
分片没变 → 整个 ingest 阶段跳过，transform 零执行。

## 已接入

| 源 | 模块 | 状态 |
|---|---|---|
| wiki | `ingest/wiki.py` | ✅ T5：`wikimedia/wikipedia` 20231101.zh 分片 → `data/raw/wiki/ingest/`，验收见 `docs/ingest-wiki.md` |
| oscar / news / pdf | — | ⬜ T6 + 后续任务书 |
