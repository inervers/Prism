"""搜索工具实现：Dummy（离线）、DuckDuckGo（真实搜索）与 Local（本地行业知识库）。

W1 默认 dummy：让骨架不依赖网络即可跑通，验证编排逻辑。
DuckDuckGo 实现作为真实搜索的后备。
Local 是行业接入的第 0 步：把爬虫/整理好的行业数据（客户官网文本、
招聘岗位记录等）统一成 JSON 格式灌入，researcher 即可检索本地库。
"""

from __future__ import annotations

import abc
import json
import re
import urllib.parse
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from prism.config import settings


class SearchResult:
    """单条搜索结果。"""

    def __init__(self, title: str, url: str, snippet: str):
        self.title = title
        self.url = url
        self.snippet = snippet

    def __repr__(self) -> str:
        return f"SearchResult({self.title!r})"


class SearchTool(abc.ABC):
    """搜索工具接口。Researcher 节点只依赖这个抽象。"""

    name: str = "search"

    @abc.abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """按查询词返回搜索结果列表。"""

    def __call__(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return self.search(query, max_results)


class DummySearch(SearchTool):
    """离线搜索：返回与查询词相关的模拟结果，用于骨架联调。"""

    name = "dummy_search"

    # 内置一个小知识库，方便不联网也能看到"证据聚合"效果
    _KB = [
        ("虚拟电厂（VPP）概念与价值", "虚拟电厂（VPP）通过聚合分布式能源、储能与可控负荷，"
         "形成可调度的电力资源参与电网调节与市场交易。"),
        ("AI Agent 定义与核心能力", "AI Agent 是能感知环境、自主决策并调用工具完成任务的大模型应用形态，"
         "核心能力包括规划、工具调用与记忆。"),
        ("RAG 检索增强生成原理", "RAG（检索增强生成）先检索外部知识库，再把检索结果作为上下文交给大模型生成，"
         "缓解幻觉并引入最新知识。"),
        ("LangGraph 状态机编排", "LangGraph 用图结构表达 Agent 工作流，支持循环、并行与人工介入，"
         "是 LangChain 团队的底层编排运行时。"),
        ("多 Agent 协作模式", "多 Agent 系统通过 Supervisor 调度、并行子 Agent 或流水线协作完成任务，"
         "分工提升单 Agent 工具过载带来的退化。"),
    ]

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        results: list[SearchResult] = []
        # 简单字符级相关：查询词与知识库标题有共同字符（中文按字判断）
        q_chars = set(query)
        for title, snippet in self._KB:
            overlap = len(q_chars & set(title))
            if overlap >= 2:
                results.append(SearchResult(title, "https://dummy.local/kb", snippet))
        return results[:max_results]


def _clean_ddg_url(url: str) -> str:
    """把 DuckDuckGo 重定向链接还原为真实 URL。"""
    if "duckduckgo.com/l/" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        target = qs.get("uddg", [""])[0]
        if target:
            return urllib.parse.unquote(target)
    return url


class DuckDuckGoSearch(SearchTool):
    """真实搜索：DuckDuckGo HTML 端点，免费无需 API key。

    proxy 为空时直连（国内大概率超时）；设置后走代理（如 Verge 127.0.0.1:7897）。
    """

    name = "duckduckgo_search"
    _BASE = "https://html.duckduckgo.com/html/"

    def __init__(self, proxy: str = ""):
        self.proxy = proxy

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        params = {"q": query}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        # 轻量重试：网络波动时重试 1 次
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = httpx.get(
                    self._BASE,
                    params=params,
                    headers=headers,
                    timeout=15,
                    follow_redirects=True,
                    proxy=self.proxy or None,
                )
                resp.raise_for_status()
                break
            except Exception as exc:  # noqa: BLE001 - 搜索失败不致命
                last_exc = exc
        else:
            return [SearchResult(f"搜索失败: {last_exc}", "", "")]

        soup = BeautifulSoup(resp.text, "html.parser")
        results: list[SearchResult] = []
        for item in soup.select(".result")[:max_results]:
            a = item.select_one(".result__a")
            snip = item.select_one(".result__snippet")
            if a is None:
                continue
            title = a.get_text(strip=True)
            url = a.get("href", "")
            if url.startswith("//"):
                url = "https:" + url
            snippet = snip.get_text(strip=True) if snip else ""
            results.append(SearchResult(title, _clean_ddg_url(url), snippet))
        return results


class LocalSearchTool(SearchTool):
    """本地行业知识库检索：从 JSON 文件读取行业数据，关键词匹配检索。

    数据格式（JSON 数组，每条一个行业素材）：
    [
      {
        "id": "c001",
        "title": "客户官网-产品线介绍",
        "content": "正文内容……",
        "source": "https://example.com",
        "tags": ["外贸", "客户", "产品"]
      }
    ]

    外贸方向：灌客户官网/产品资料文本；招聘方向：灌岗位记录（title=岗位名，
    content=JD 要点，tags=技能/城市）。检索用中文 bigram + 英文单词关键词
    匹配打分（标题命中×3、标签×2、正文×1），对结构化素材足够且零成本。
    """

    name = "local_search"

    def __init__(self, kb_path: str = ""):
        self.kb_path = kb_path or str(settings.local_kb_path)
        self._kb = self._load(self.kb_path)

    def _load(self, path: str) -> list[dict]:
        p = Path(path)
        if not p.exists():
            print(f"[local_search] 知识库不存在: {p}（行业数据未灌入）")
            return []
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _split_keywords(query: str) -> list[str]:
        """查询词切分：先按空白/标点切 token，中文段转 2-gram，英文段取单词。"""
        kws: list[str] = []
        for part in re.split(r"[\s,，。、;；:：\-/()（）]+", query):
            part = part.strip()
            if not part:
                continue
            # 中文段 → 2-gram；英文段 → 单词
            for zh in re.findall(r"[\u4e00-\u9fff]+", part):
                if len(zh) <= 2:
                    kws.append(zh)
                else:
                    kws.extend(zh[i : i + 2] for i in range(len(zh) - 1))
            kws.extend(e.lower() for e in re.findall(r"[a-z0-9]+", part, re.IGNORECASE))
        return [k for k in kws if k]

    @staticmethod
    def _score(item: dict, keywords: list[str]) -> int:
        title = item.get("title", "")
        content = item.get("content", "")
        tags = " ".join(item.get("tags", []))
        score = 0
        for kw in keywords:
            if kw in title:
                score += 3
            if kw in tags:
                score += 2
            if kw in content:
                score += 1
        return score

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not self._kb:
            # 空库：返回空结果（researcher 会过滤掉 → 走 no_evidence 短路）
            return []
        keywords = self._split_keywords(query)
        scored = [
            (self._score(item, keywords), item)
            for item in self._kb
            if self._score(item, keywords) > 0
        ]
        scored.sort(key=lambda x: -x[0])
        return [
            SearchResult(
                item.get("title", ""),
                item.get("source", ""),
                item.get("content", ""),
            )
            for _, item in scored[:max_results]
        ]


def build_search_tool(backend: str, proxy: str = "") -> SearchTool:
    """按配置构建搜索工具。"""
    if backend == "duckduckgo":
        return DuckDuckGoSearch(proxy=proxy)
    if backend == "local":
        return LocalSearchTool()
    return DummySearch()
