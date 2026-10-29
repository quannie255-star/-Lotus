"""ingest — 各源适配器（薄接缝：只映射，不清洗，不产生 changelog）

四个源长得完全不同，对下游的承诺只有一条：`iter_docs(...) -> Iterator[spine 字典]`。
新增源 = 新增一个模块 + registry 里一条记录，不改本包之外任何代码。
"""
