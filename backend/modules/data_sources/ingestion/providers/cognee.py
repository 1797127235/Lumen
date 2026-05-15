"""CogneeProvider — DocumentIndexProvider 的 Cognee 实现。

双阶段架构（全部内聚于此）：
1. sync_document → cognee.add() 写入原始数据 → mark_needs_cognify()
2. _cognify_loop (后台) → cognee.cognify() 分块/向量化/建图

两条路径的协作关系：
- _sync_narrative_to_provider (projection.py) 通过 DocumentIndexProvider
  接口调用 sync_document，不感知底层是 Cognee 还是 LanceDB
- CogneeProvider 内部自行管理 cognify 生命周期：初始化、add、cognify 循环
- 其他 Provider（LanceDB/HRR）在 sync_document 内一步完成，无需后台循环
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from backend.core.config import USER_DATA_DIR
from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.document_index_provider import DocumentIndexProvider, ProviderHit

logger = get_logger(__name__)


class CogneeProvider(DocumentIndexProvider):
    """Cognee 实现。内部自动分块、向量化、建图。

    分块策略：Cognee 内部处理，外部不感知。
    """

    # ── 类级别状态（跨实例共享）─────────────────────────────────────
    _status: str = "not_initialized"
    _needs_cognify: bool = False
    _bg_task: asyncio.Task | None = None
    _COGNIFY_INTERVAL_SEC: int = int(os.environ.get("COGNEE_COGNIFY_INTERVAL_SEC", "60"))

    # ── DocumentIndexProvider 接口 ──────────────────────────────────

    @classmethod
    def provider_name(cls) -> str:
        return "cognee"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import cognee  # noqa: F401

            return True
        except ImportError:
            return False

    @classmethod
    def status(cls) -> str:
        """Cognee 索引状态：'ready' | 'not_initialized' | 'not_installed' | 'error'。"""
        return cls._status

    # ── 初始化 ─────────────────────────────────────────────────────

    def initialize(self) -> None:
        """初始化 Cognee 配置，启动后台 cognify 循环。"""
        self._init_cognee()
        self._ensure_bg_loop()

    @classmethod
    def _init_cognee(cls) -> str:
        """配置 Cognee SDK：LLM provider、embedding provider、data root。"""
        os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"

        try:
            import cognee as _cognee

            from backend.core.config import get_settings

            settings = get_settings()
            _cognee.config.data_root_directory(str(USER_DATA_DIR))

            llm_api_key = settings.llm_api_key or settings.dashscope_api_key
            llm_base_url = settings.llm_base_url
            if llm_api_key and llm_base_url:
                _cognee.config.set_llm_provider("openai")
                _cognee.config.set_llm_model(settings.llm_model)
                _cognee.config.set_llm_api_key(llm_api_key)
                _cognee.config.set_llm_endpoint(llm_base_url)

            embedding_api_key = settings.embedding_api_key or llm_api_key
            embedding_base_url = settings.embedding_base_url
            if embedding_api_key and embedding_base_url:
                _cognee.config.set_embedding_provider("openai")
                embedding_model = settings.embedding_model or "text-embedding-3-small"
                try:
                    import tiktoken

                    tiktoken.encoding_for_model(embedding_model)
                except KeyError:
                    tiktoken.model.MODEL_TO_ENCODING[embedding_model] = "cl100k_base"  # type: ignore[possiblyUnbound]
                _cognee.config.set_embedding_model(embedding_model)
                _cognee.config.set_embedding_api_key(embedding_api_key)
                _cognee.config.set_embedding_endpoint(embedding_base_url)

            cls._status = "ready"
            logger.info(
                "Cognee initialized",
                data_root=str(USER_DATA_DIR),
                llm_model=settings.llm_model,
                embedding_model=settings.embedding_model,
            )
            return cls._status
        except ImportError:
            logger.warning("Cognee not installed")
            cls._status = "not_installed"
            return cls._status
        except Exception as exc:
            logger.error("Cognee init failed", error=str(exc))
            cls._status = "error"
            return cls._status

    # ── 后台 cognify 循环 ──────────────────────────────────────────

    @classmethod
    def _mark_needs_cognify(cls) -> None:
        cls._needs_cognify = True

    @classmethod
    def _ensure_bg_loop(cls) -> None:
        """惰性启动后台 cognify 循环（仅当有运行中的事件循环时）。"""
        if cls._bg_task is not None and not cls._bg_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
            if not loop.is_running():
                return
        except RuntimeError:
            return
        cls._bg_task = loop.create_task(cls._cognify_loop(), name="cognee-cognify")
        cls._bg_task.add_done_callback(lambda _: logger.debug("Cognee cognify loop finished"))

    @classmethod
    async def _cognify_loop(cls) -> None:
        """后台轮询：_needs_cognify 置位时调用 cognee.cognify() 处理增量数据。"""
        from backend.modules.memory.datasets import ALL_DATASETS

        while True:
            await asyncio.sleep(cls._COGNIFY_INTERVAL_SEC)
            if not cls._needs_cognify or cls._status != "ready":
                continue

            cls._needs_cognify = False
            try:
                import cognee as _cognee

                await _cognee.cognify(datasets=ALL_DATASETS)
                logger.info("Cognee cognify completed", datasets=str(ALL_DATASETS))
            except Exception as exc:
                logger.error("Cognee cognify failed", error=str(exc))
                cls._needs_cognify = True

    # ── 搜索 ───────────────────────────────────────────────────────

    async def prefetch(self, query: str) -> list[ProviderHit]:
        import cognee as _cognee

        from backend.modules.memory.datasets import ALL_DATASETS

        self._ensure_bg_loop()
        try:
            raw = await _cognee.recall(query, datasets=ALL_DATASETS, top_k=10)
            if not raw:
                return []
            hits: list[ProviderHit] = []
            for i, r in enumerate(raw):
                text = self._extract_text(r)
                if not text:
                    continue
                doc_id = getattr(r, "id", None) or getattr(r, "doc_id", None) or f"cognee:{i}"
                score = float(getattr(r, "score", 0.0) or 0.0)
                hits.append(ProviderHit(doc_id=str(doc_id), content=text[:500], score=score))
            return hits
        except Exception as exc:
            logger.warning("Cognee search failed", query=query, error=str(exc))
            return []

    # ── 写入（双阶段：add → cognify）───────────────────────────────

    async def sync_document(
        self,
        content: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """阶段一：cognee.add() 写入原始数据，标记需要 cognify。"""
        import cognee as _cognee

        from backend.modules.memory.datasets import DATASET_KNOWLEDGE

        self._ensure_bg_loop()
        dataset = metadata.get("dataset", DATASET_KNOWLEDGE) if metadata else DATASET_KNOWLEDGE
        try:
            await _cognee.add(content, dataset_name=dataset)
            self._mark_needs_cognify()
            logger.debug("Cognee add ok", doc_id=doc_id, dataset=dataset, content_length=len(content))
        except Exception as exc:
            logger.error("Cognee ingest failed", doc_id=doc_id, dataset=dataset, error=str(exc))

    # ── 管理 ───────────────────────────────────────────────────────

    async def delete_document(self, doc_id: str) -> bool:
        logger.warning("Cognee per-document deletion not supported", doc_id=doc_id)
        return False

    async def clear(self) -> bool:
        """清空 Cognee 索引（删除底层存储目录并重新初始化）。"""
        try:
            for name in ("kuzu", "lancedb"):
                path = Path(USER_DATA_DIR) / name
                if path.exists():
                    shutil.rmtree(path, ignore_errors=False)
            self._init_cognee()
            logger.info("Cognee index cleared and reinitialized")
            return True
        except Exception as exc:
            logger.error("Cognee clear failed", error=str(exc))
            return False

    # ── 工具 ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(result: Any) -> str | None:
        for attr in ("text", "answer", "context", "content"):
            val = getattr(result, attr, None)
            if val:
                return str(val)
        return None

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "data_source_search",
                "description": "搜索外部数据源中的相关文档",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            }
        ]

    async def handle_tool_call(self, name: str, args: dict) -> str:
        if name == "data_source_search":
            hits = await self.prefetch(args["query"])
            if not hits:
                return "未找到相关内容。"
            return "\n\n".join(f"[来源: {h.doc_id}]\n{h.content}" for h in hits)
        raise NotImplementedError(f"Tool {name} not supported")
