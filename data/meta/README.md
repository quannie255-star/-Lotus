# data/meta — 运行期状态（本地，不进 git）

这里放**运行期产生的状态**，不是源码；`.gitignore` 忽略内容，只保留本 README 说明目录结构。

| 路径 | 内容 | 能否重建 | 谁写 |
|---|---|---|---|
| `runs.jsonl` | 每个 stage 的 start/finish 事件（审计链） | **不能**（丢了就是丢证据） | `common/runs.py` |
| `first_seen/<source>.json` | `doc_id → 首次入库时间` 台账 | 不能，但可以按 `first_seen_at` 回落（重跑会重新取时间，语义变差） | `common/first_seen.py` |

两条纪律：

1. **不进 git**：`runs.jsonl` 是审计证据不是配置；`first_seen` 台账 23 万条 21.9 MB，
   进 git 等于把数据放仓库。正式环境这两样该跟对象存储/数据库对齐（W7 的 `meta.runs` 表）。
2. **写一半必须能被发现**：台账先写 `.tmp` 再 `replace`；`runs.jsonl` 只追加、坏行报行号。

规模提醒：ledger 现在是一整块 JSON，6 个分片（≈140 万条）时该换 SQLite 或按 source 分桶。
