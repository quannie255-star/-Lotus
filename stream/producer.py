"""stream/producer.py — Kafka 增量模拟生产者（M9）

路线（免装 kafka 客户端依赖）：Python 生成 spine 行 JSON → 管道喂给 kafka 容器内的
kafka-console-producer。doc_id 确定性（source=stream-sim + 行 id）→ 重放数据会被下游
dedup 自然折叠——流式入口的幂等不是靠去重器，是靠身份确定性（README 要讲这个设计）。

Spark Structured Streaming 消费作业（kafka→raw 分区）需要 spark 容器加装
spark-sql-kafka 包，属 W7 完整化项——当前先打通「生产→落 Kafka→可消费验证」。
"""

import json
import subprocess
import sys
from pathlib import Path

from common import ids, log
from common.textstats import text_stats

ROOT = Path(__file__).resolve().parent.parent
TOPIC = "corpus-ingest"
SIM_SOURCE = "stream-sim"


def build_messages(n: int = 20, seed: int = 7) -> list:
    """确定性模拟增量行（spine 最小集 + doc_id）。同 (n, seed) 同输出。

    参数: n — 条数；seed — 种子
    返回: JSON 消息列表（str）
    异常: 无
    幂等: 纯函数；重放同批消息 = 同 doc_id 集 = 下游去重折叠
    """
    import random

    rng = random.Random(seed)
    msgs = []
    for i in range(n):
        content = f"流式增量第{i}条：{rng.choice(['市场', '数据', '管道'])}消息正文，用于模拟接入。" * 3
        stats = text_stats(content)
        source_id = f"sim-{seed}-{i:05d}"
        msgs.append(json.dumps({
            "doc_id": ids.spine_doc_id(SIM_SOURCE, source_id),
            "source": SIM_SOURCE, "source_id": source_id, "content": content,
            "content_hash": ids.content_hash(content),
            "char_count": stats["char_count"], "cjk_char_count": stats["cjk_char_count"],
        }, ensure_ascii=False))
    return msgs


def produce(messages: list, topic: str = TOPIC, timeout: int = 60) -> dict:
    """把消息喂进 Kafka（console-producer 通道）。

    参数: messages — JSON 行列表；topic — 主题
    返回: {"produced": n, "topic": topic}
    异常: subprocess.CalledProcessError — kafka 容器/主题不可用（stderr 附在输出里）
    幂等: 重复 produce 同批消息 → 同 doc_id → 下游折叠（Kafka 层不去重，设计如此）
    """
    payload = "\n".join(messages)
    cmd = ["docker", "compose", "exec", "-T", "kafka",
           "/opt/bitnami/kafka/bin/kafka-console-producer.sh",
           "--bootstrap-server", "localhost:9092", "--topic", topic]
    proc = subprocess.run(cmd, cwd=ROOT, input=payload, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"produce 失败: {proc.stderr[-500:]}")
    return {"produced": len(messages), "topic": topic}


def consume_peek(topic: str = TOPIC, n: int = 5, timeout: int = 30) -> list:
    """从 topic 头部取 n 条验证可消费性（console-consumer，--from-beginning --max-messages）。"""
    cmd = ["docker", "compose", "exec", "-T", "kafka",
           "/opt/bitnami/kafka/bin/kafka-console-consumer.sh",
           "--bootstrap-server", "localhost:9092", "--topic", topic,
           "--from-beginning", "--max-messages", str(n), "--timeout-ms", "5000"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"consume 失败: {proc.stderr[-500:]}")
    return [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]


def main(argv=None):  # pragma: no cover
    log.setup()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    result = produce(build_messages(n))
    peek = consume_peek(n=3)
    print(json.dumps({**result, "peek_first_doc": json.loads(peek[0])["doc_id"] if peek else None,
                      "peek_lines": len(peek)}, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    main()
