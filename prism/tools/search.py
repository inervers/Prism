"""搜索工具实现：Dummy（离线）与 DuckDuckGo（真实，免费无需 key）。

W1 默认 dummy：让骨架不依赖网络即可跑通，验证编排逻辑。
DuckDuckGo 实现作为真实搜索的后备，W2 可替换为 spider-nexus 爬虫。
"""

from __future__ import annotations

import abc
import urllib.parse

import httpx
from bs4 import BeautifulSoup


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
        except Exception as exc:  # noqa: BLE001 - 搜索失败不致命
            return [SearchResult(f"搜索失败: {exc}", "", "")]

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


def build_search_tool(backend: str, proxy: str = "") -> SearchTool:
    """按配置构建搜索工具。"""
    if backend == "duckduckgo":
        return DuckDuckGoSearch(proxy=proxy)
    return DummySearch()
