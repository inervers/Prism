"""LLM 客户端：基于 langchain-openai 封装 DeepSeek。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from prism.config import settings


def build_llm(temperature: float = 0.3) -> ChatOpenAI:
    """构建 DeepSeek 客户端（OpenAI 兼容接口）。"""
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=temperature,
    )
