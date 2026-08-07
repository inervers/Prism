"""上下文预算与压缩（context compaction）。

深度研究长任务里，证据材料可能撑爆单次 LLM 调用的上下文窗口。
本模块在 Writer 构造输入前做预算监控，超限时对证据做确定性压缩。

设计原则：
- 预算按"单个章节的 LLM 调用"计算（Prism 的上下文膨胀点是章节生成
  要把该子问题的全部证据拼进 prompt，重写循环还会反复喂）
- 压缩策略 = 首部保留截断：证据正文头部信息密度最高，保留头部砍尾部；
  只截断不丢弃，保证引用编号 [子问题-序号] 完整性（丢弃会破坏编号集合）
- 零 LLM 成本：不引入额外的摘要调用（那是"用钱换钱"），确定性截断即可；
  LLM 摘要模式留作扩展点（COMPACT_MODE=summarize，未实现）
- 可观测：Writer 的 trace 记录压缩前后字符量与压缩标记
"""

from __future__ import annotations

from prism.state import Evidence

# 每条证据压缩后的最小保留长度（字符）
MIN_CONTENT_CHARS = 150
# 单次截断：最长证据截到其 40%（保留头部）
TRUNCATE_RATIO = 0.4
# 截断标记：提示 LLM 材料不完整，避免编造被砍掉的细节
TRUNCATE_SUFFIX = "\n[材料过长已截断]"


def estimate_chars(evs: list[Evidence]) -> int:
    """估算一组证据的总字符量（用于预算判断）。"""
    return sum(len(ev.get("content", "")) for ev in evs)


def compact_evidence(
    evs: list[Evidence], budget_chars: int
) -> tuple[list[Evidence], bool]:
    """证据超预算时做首部保留截断。

    返回 (压缩后的证据列表, 是否发生了压缩)。
    浅拷贝，不污染 state；只截断不丢弃，每条至少 MIN_CONTENT_CHARS。
    """
    if estimate_chars(evs) <= budget_chars:
        return evs, False

    result = [dict(ev) for ev in evs]  # 浅拷贝；content 为 str 不可变，安全
    changed = False
    while estimate_chars(result) > budget_chars:
        longest = max(result, key=lambda ev: len(ev.get("content", "")))
        content = longest.get("content", "")
        # 已截断过的先去后缀再截，避免 [材料过长已截断] 叠加，且允许反复截到下限
        if content.endswith(TRUNCATE_SUFFIX):
            content = content[: -len(TRUNCATE_SUFFIX)]
        if len(content) <= MIN_CONTENT_CHARS:
            break  # 全部证据已到下限仍超预算：放弃（预算设计上应能放下）
        keep = max(MIN_CONTENT_CHARS, int(len(content) * TRUNCATE_RATIO))
        longest["content"] = content[:keep] + TRUNCATE_SUFFIX
        changed = True
    return result, changed
