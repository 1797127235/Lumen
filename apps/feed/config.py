"""订阅信息流应用 — 配置读取（环境变量）。

订阅应用独立于 Lumen，自带 LLM 配置，不碰 Lumen 的 config。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # LLM（独立配置）
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout: int
    # Server
    host: str
    port: int
    # 存储
    db_path: Path
    # 调度
    poll_interval_min: int
    poll_limit_per_feed: int
    # 分析
    analyze_batch_size: int


def load_settings() -> Settings:
    home = Path(os.environ.get("LUMEN_HOME", str(Path.home() / ".lumen")))
    db_path = Path(os.environ.get("FEED_DB_PATH", str(home / "feed" / "feed.db")))
    return Settings(
        llm_api_key=os.environ.get("FEED_LLM_API_KEY", ""),
        llm_base_url=os.environ.get("FEED_LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_model=os.environ.get("FEED_LLM_MODEL", "gpt-4o-mini"),
        llm_timeout=int(os.environ.get("FEED_LLM_TIMEOUT", "60")),
        host=os.environ.get("FEED_HOST", "127.0.0.1"),
        port=int(os.environ.get("FEED_PORT", "8765")),
        db_path=db_path,
        poll_interval_min=int(os.environ.get("FEED_POLL_INTERVAL_MIN", "30")),
        poll_limit_per_feed=int(os.environ.get("FEED_POLL_LIMIT_PER_FEED", "20")),
        analyze_batch_size=int(os.environ.get("FEED_ANALYZE_BATCH", "8")),
    )
