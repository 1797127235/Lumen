"""Embedding 客户端"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BATCH_SIZE = 10


class EmbeddingError(Exception):
    """Embedding 调用失败。"""


@dataclass
class EmbeddingConfig:
    """Embedding 配置。"""

    api_key: str = ""
    base_url: str = ""
    model: str = ""


class AsyncEmbeddingClient:
    """异步 Embedding 客户端。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        if not api_key:
            raise EmbeddingError("EMBEDDING_API_KEY 未配置")
        if not base_url:
            raise EmbeddingError("EMBEDDING_BASE_URL 未配置")
        if not model:
            raise EmbeddingError("EMBEDDING_MODEL 未配置")

        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本 embedding。"""
        if not texts:
            return []

        results: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            results.extend(await self._embed_batch(batch))
        return results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """获取单个批次的 embedding。"""

        try:
            resp = await self._client.post(
                "/embeddings",
                json={"model": self._model, "input": texts},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Embedding 请求失败: %s", exc)
            raise EmbeddingError(f"Embedding request failed: {exc}") from exc

        data = resp.json()
        embeddings = data.get("data", [])
        if len(embeddings) != len(texts):
            raise EmbeddingError("Embedding response size mismatch")
        embeddings.sort(key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in embeddings]

    async def embed_one(self, text: str) -> list[float]:
        """获取单条文本 embedding。"""
        results = await self.embed([text])
        if not results:
            raise EmbeddingError("Empty embedding response")
        return results[0]

    async def close(self) -> None:
        await self._client.aclose()


def build_embedder(config: EmbeddingConfig | None = None) -> AsyncEmbeddingClient:
    """构建 Embedding 客户端。

    优先使用 config 参数，其次从环境变量读取。
    """
    if config is not None:
        api_key = config.api_key
        base_url = config.base_url
        model = config.model
    else:
        api_key = os.environ.get("EMBEDDING_API_KEY", "")
        base_url = os.environ.get("EMBEDDING_BASE_URL", "")
        model = os.environ.get("EMBEDDING_MODEL", "")

    return AsyncEmbeddingClient(api_key=api_key, base_url=base_url, model=model)
