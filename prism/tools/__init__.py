"""工具层：SearchTool 抽象 + 实现类。

包根只做再导出；具体定义见 tools/search.py。
"""

from prism.tools.search import (  # noqa: F401
    DummySearch,
    DuckDuckGoSearch,
    SearchResult,
    SearchTool,
    build_search_tool,
)
