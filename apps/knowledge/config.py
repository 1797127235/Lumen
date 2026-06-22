"""知识库配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class KnowledgeConfig:
    """知识库配置。"""

    # MCP Server
    host: str = "127.0.0.1"
    port: int = 8766

    # 数据库
    db_path: str = ""

    # Embedding
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = ""

    # 分块
    chunk_size: int = 800
    chunk_overlap: int = 100
    chunk_min_size: int = 80

    # 检索
    default_top_k: int = 4
    score_threshold: float = 0.1

    def __post_init__(self) -> None:
        if not self.db_path:
            self.db_path = str(Path.home() / ".lumen" / "knowledge.db")


def load_config() -> KnowledgeConfig:
    """从环境变量加载配置。"""
    return KnowledgeConfig(
        host=os.environ.get("KNOWLEDGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("KNOWLEDGE_PORT", "8766")),
        db_path=os.environ.get("KNOWLEDGE_DB_PATH", ""),
        embedding_api_key=os.environ.get("EMBEDDING_API_KEY", ""),
        embedding_base_url=os.environ.get("EMBEDDING_BASE_URL", ""),
        embedding_model=os.environ.get("EMBEDDING_MODEL", ""),
        chunk_size=int(os.environ.get("KNOWLEDGE_CHUNK_SIZE", "800")),
        chunk_overlap=int(os.environ.get("KNOWLEDGE_CHUNK_OVERLAP", "100")),
        default_top_k=int(os.environ.get("KNOWLEDGE_TOP_K", "4")),
        score_threshold=float(os.environ.get("KNOWLEDGE_SCORE_THRESHOLD", "0.1")),
    )
