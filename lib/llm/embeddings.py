"""Embedding 客户端 — OpenAI 兼容 /embeddings API。"""

from __future__ import annotations

import httpx

from shared.logging import get_logger

logger = get_logger(__name__)


class EmbeddingError(Exception):
    """Embedding 调用失败。"""


class AsyncEmbeddingClient:
    """异步 Embedding 客户端。"""

    def __init__(self, api_key: str, base_url: str, model: str):
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

        try:
            resp = await self._client.post(
                "/embeddings",
                json={
                    "model": self._model,
                    "input": texts,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Embedding 请求失败", error=str(exc))
            raise EmbeddingError(f"Embedding request failed: {exc}") from exc

        data = resp.json()
        embeddings = data.get("data", [])
        # 按 index 排序
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


def build_embedding_client(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> AsyncEmbeddingClient:
    """从参数或 settings 构建 Embedding 客户端。"""
    from core.config import get_settings

    settings = get_settings()

    key = api_key or settings.embedding_api_key or settings.llm_api_key or ""
    url = base_url or settings.embedding_base_url or settings.llm_base_url or ""
    mdl = model or settings.embedding_model or ""

    if not key:
        logger.warning("Embedding API key 未配置")
    if not url:
        logger.warning("Embedding base_url 未配置")
    if not mdl:
        logger.warning("Embedding model 未配置")

    return AsyncEmbeddingClient(api_key=key, base_url=url, model=mdl)
