"""Aggregator 节点：按子问题对证据去重、归组。

去重策略 v2：以规范化 URL 为键（同一来源跨子问题重复只保留一次）。
W3 可升级为 LLM 语义去噪（不同 URL 但内容重复）。
"""

from __future__ import annotations

import urllib.parse

from prism.state import Evidence, PrismState


def _normalize_url(url: str) -> str:
    """URL 规范化：去 fragment、query 参数排序，识别同一来源。"""
    try:
        parsed = urllib.parse.urlparse(url)
        path = parsed.netloc + parsed.path
        if path.endswith("/"):
            path = path[:-1]
        return path.lower()
    except Exception:  # noqa: BLE001 - 解析失败时退回原样
        return url


def _dedup(evidences: list[Evidence]) -> list[Evidence]:
    """按规范化 URL 去重：同一来源只保留第一条。"""
    seen: set[str] = set()
    result: list[Evidence] = []
    for ev in evidences:
        key = _normalize_url(ev.get("source", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(ev)
    return result


def aggregator_node(state: PrismState) -> dict:
    # 先全局按 URL 去重（同源跨子问题只留一次），再按子问题归组
    deduped = _dedup(state.get("evidence", []))

    grouped: dict[str, list[Evidence]] = {}
    for ev in deduped:
        grouped.setdefault(ev["sub_id"], []).append(ev)

    return {
        "grouped_evidence": grouped,
        "trace": [
            {
                "node": "aggregator",
                "detail": (
                    f"raw={len(state.get('evidence', []))} "
                    f"url_deduped={len(deduped)} "
                    f"subs_with_evidence={len(grouped)}"
                ),
                "tokens": 0,
            }
        ],
    }
