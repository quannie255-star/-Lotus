"""common/first_seen.py — first_seen_at 台账（W3 ingest 前置）

schema 的 policy.time 写着：`first_seen_at` 为首次入库时间，重跑沿用旧值，参与幂等校验。
如果每跑一次都写 `now()`，这条 policy 就是假的，而且 T4 承诺的
"同样输入两次跑出的文件字节一致"会当场失效——字节里躺着一个每次都变的时钟。

所以首次入库时间必须**落一次盘**：doc_id → 第一次被 ingest 看到的时间。
- 命中台账：沿用旧值（这就是"参与幂等校验"）
- 未命中：取当前时间，记入待提交区
- commit()：与旧台账合并后原子替换（先写 .tmp 再 replace，同 idempotent._write_manifest）

边界：台账在 transform 成功返回后才 commit。若 transform 中途失败，本次分配的时间不落盘
——数据没有发布，就不该有"首次入库"这回事；下次重跑重新取时间。
"""

import json
from pathlib import Path

from common import runs


class FirstSeenLedger:
    """doc_id → 首次入库 ISO 时间的追加式台账。"""

    def __init__(self, path, clock=None):
        self.path = Path(path)
        self._clock = clock or runs.now_iso
        self._seen = self._load()
        self._pending = {}

    def _load(self):
        """读历史台账；坏 JSON 直接炸——台账对不上，宁可不跑也不覆盖。"""
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} 不是 {{doc_id: timestamp}} 的形状")
        for doc_id, stamp in data.items():
            if not isinstance(stamp, str):
                raise ValueError(f"{self.path} 里 {doc_id} 的时间不是字符串: {stamp!r}")
        return data

    def resolve(self, doc_id):
        """取首次入库时间：先查历史，再查本次待提交，最后才取现在。"""
        if doc_id in self._seen:
            return self._seen[doc_id]
        if doc_id not in self._pending:
            self._pending[doc_id] = self._clock()
        return self._pending[doc_id]

    def commit(self):
        """把本次新分配的条目合并进台账；没有新条目就不动文件（重跑零写入）。"""
        if not self._pending:
            return False
        merged = dict(self._seen)
        merged.update(self._pending)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(self.path)
        self._seen = merged
        self._pending = {}
        return True

    def __len__(self):
        return len(self._seen) + len(self._pending)
