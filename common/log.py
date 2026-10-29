"""common/log.py — 全项目日志约定（DEVLOG M1-D6）

为什么需要它：MANUAL 反查表要求「改日志级别」有真实落点；此前模块用 print，
级别不可调、无时间戳、无法区分来源。

微调入口：环境变量 LOG_LEVEL（DEBUG/INFO/WARNING/ERROR，默认 INFO）。
见 MANUAL.md 反查表「改日志级别」行。

约定：
- 库代码（common/、ingest/ 等）只 get_logger(__name__)，永不配置 root logger；
- 配置发生在入口（main / smoke），入口也是唯一 print 的地方（面向人的摘要）；
- 日志写 stderr——stdout 留给结构化输出（json 摘要、pytest 结果），管道友好。
"""

import logging
import os
import sys

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    """取命名 logger；不配置 handler（配置是入口的职责）。

    参数: name — 惯例用 __name__
    返回: logging.Logger
    异常: 无（级别非法时回退 INFO 并警告一次，而不是炸掉调用方）
    幂等: 重复调用返回同一 logger 对象（logging 单例语义）
    """
    return logging.getLogger(name)


def setup(level: str | None = None) -> None:
    """入口调用一次：按 LOG_LEVEL 配置 root handler（写 stderr）。

    参数: level — 显式级别；None 则读环境变量 LOG_LEVEL，再缺省 INFO
    返回: 无
    异常: 无（非法级别回退 INFO——日志配置错误不该有能力杀死管道）
    幂等: 是——重复调用不会叠加 handler（先清空 root handlers）
    """
    chosen = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    if chosen not in _LEVELS:
        logging.getLogger(__name__).warning("LOG_LEVEL=%r 非法，回退 INFO", chosen)
        chosen = "INFO"

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    root.setLevel(chosen)
