# data/raw

职责：**原始不可变层**。ingest 的产物落这里，一个字节都不清洗；clean 从这里读，往
`data/staging/` 写，并在这里之外记 changelog（ingest 零 changelog）。

## 本地布局（两段式写入的第一段）

```
data/raw/<source>/manifest.json          ← T4 幂等台账（输入指纹 + 产物 sha256）
data/raw/<source>/ingest/part-00000.parquet
```

`ingest/` 是 `run_stage` 的 stage 目录（临时目录构建 → 原子 rename），
所以"写了一半"不会出现在这里；目录存在且 manifest 哈希对得上，才允许命中缓存。

## 对象存储布局（第二段：mc cp 上传）

```
s3a://raw/docs/source=wiki/part-00000.parquet     ← hive 式分区目录
s3a://raw/docs/source=oscar/part-00000.parquet
```

`docs/` 这一级不是装饰：Hive 4 拒绝把**桶根**当表位置
（`InvalidObjectException: location must not be root path`），表必须指向
`LOCATION 's3a://raw/docs/'`；分区目录名里的 `source` 才是表定义里的分区列，
`WHERE source='wiki'` 自动裁掉别的目录。**存储布局服务查询模式**，不写一行转换代码。
（实测记录见 `docs/ingest-wiki.md` 第八节、`docs/dev-lessons.md` #006 坑二/坑三。）

本地不直接 s3fs 写远端：少一个依赖，多一份本地可检查性（上传前能用 pyarrow 打开验货）。
