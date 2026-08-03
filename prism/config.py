"""全局配置：从 .env 读取，带默认值。"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（Prism/）
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class Settings:
    api_key: str = _get("DEEPSEEK_API_KEY", "")
    base_url: str = _get("LLM_BASE_URL", "https://api.deepseek.com")
    model: str = _get("LLM_MODEL", "deepseek-v4-flash")
    search_backend: str = _get("SEARCH_BACKEND", "dummy")
    search_proxy: str = _get("SEARCH_PROXY", "")  # 如 http://127.0.0.1:7897
    local_kb_path: str = _get(
        "LOCAL_KB_PATH", str(ROOT_DIR / "data" / "industry_kb.json")
    )
    memory_db_path: str = _get(
        "MEMORY_DB_PATH", str(ROOT_DIR / "data" / "prism_memory.db")
    )

    # 研究参数
    max_subquestions: int = int(_get("MAX_SUBQUESTIONS", "5"))
    max_evidence_per_sub: int = int(_get("MAX_EVIDENCE_PER_SUB", "3"))
    report_language: str = _get("REPORT_LANGUAGE", "zh")


settings = Settings()
