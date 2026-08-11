"""Prism 记忆模块：跨任务长期记忆 + SQLite checkpointer。

记忆分两层：
1. 执行状态记忆：LangGraph checkpointer（MemorySaver 或 SQLite）
   记住"任务跑到哪一步"，支持 HITL 中断恢复。
2. 长期知识记忆：SQLite 表 memories（Python 内置 sqlite3，零依赖）
   任务完成时沉淀结论（write_memory），planner 规划时检索相关历史
   （recall_memory），避免重复研究、复用沉淀结论。

存储位置：data/prism_memory.db（随项目走，可 gitignore）
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from prism.config import ROOT_DIR, settings

MEMORY_DB = Path(settings.memory_db_path)
_CHECKPOINT_DB = ROOT_DIR / "data" / "prism_checkpoints.db"


# ---------------------------------------------------------------- 长期记忆

def _connect() -> sqlite3.Connection:
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MEMORY_DB))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            topic      TEXT NOT NULL,
            summary    TEXT NOT NULL,
            sources    TEXT DEFAULT '',
            created_at REAL NOT NULL
        )
        """
    )
    return conn


def write_memory(topic: str, summary: str, sources: list[str] | None = None) -> None:
    """任务完成后沉淀一条记忆：主题 + 结论摘要 + 引用来源。"""
    if not summary:
        return
    conn = _connect()
    conn.execute(
        "INSERT INTO memories (topic, summary, sources, created_at) VALUES (?,?,?,?)",
        (topic, summary[:800], "|".join(dict.fromkeys(sources or []))[:2000], time.time()),
    )
    conn.commit()
    conn.close()


def recall_memory(topic: str, top_k: int = 3) -> list[dict]:
    """按主题相关性检索历史记忆（字符重叠打分，中文友好）。

    返回最近的、与主题有至少 2 个共同字符的记忆，按相关度排序。
    """
    conn = _connect()
    rows = conn.execute(
        "SELECT topic, summary, sources, created_at FROM memories "
        "ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    conn.close()

    q_chars = set(topic)
    scored: list[tuple[int, dict]] = []
    for t, summary, sources, ts in rows:
        overlap = len(q_chars & set(t))
        if overlap >= 2:
            scored.append(
                (
                    overlap,
                    {
                        "topic": t,
                        "summary": summary,
                        "sources": sources,
                        "created_at": ts,
                    },
                )
            )
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:top_k]]


def memory_count() -> int:
    """记忆条数（调试/验证用）。"""
    conn = _connect()
    n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    return n


# ---------------------------------------------------------------- checkpointer

def build_checkpointer(path: Path | str = _CHECKPOINT_DB):
    """构建正式运行使用的 SQLite checkpointer。

    HITL 的跨进程恢复依赖持久化，因此缺少独立依赖时直接失败；测试如需
    内存 checkpointer，应显式向 ``build_graph`` 注入 ``MemorySaver``。
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "SQLite checkpointer is required. Install with: "
            "pip install langgraph-checkpoint-sqlite"
        ) from exc

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：Send 并行 researcher 可能跨线程写 checkpoint。
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    print(f"[memory] checkpointer: SQLite ({db_path.name})")
    return saver
