"""Akasha 图记忆引擎 — Lumen MemoryProvider 插件。

基于 message-as-truth 的图记忆：
- 每轮对话生成 turn-level 节点
- Dense + RWR Ripple + Graph-expand 三层召回
- Hebbian/STDP 边生长、时间衰减、资源抑制
"""

from __future__ import annotations

from typing import Any

from lib.llm.embeddings import AsyncEmbeddingClient, build_embedding_client
from lib.memory.provider import MemoryProvider
from shared.logging import get_logger

logger = get_logger(__name__)


class Provider(MemoryProvider):
    """Akasha MemoryProvider。

    配置项（config dict）：
        db_path: str = ""               # sidecar DB 路径，默认 ~/.lumen/memory/{user_id}/akasha.db
        dense_top_k: int = 10
        ripple_top_k: int = 10
        activate_limit: int = 8
        embedding_model: str = ""       # 覆盖 settings.embedding_model
        embedding_api_key: str = ""     # 覆盖 settings.embedding_api_key
        embedding_base_url: str = ""    # 覆盖 settings.embedding_base_url
    """

    def __init__(self, **config: Any) -> None:
        super().__init__()
        self._config = config
        self._engine: Any | None = None
        self._current_user_id: str = ""

    @property
    def name(self) -> str:
        return "akasha"

    def _build_embedder(self) -> AsyncEmbeddingClient | None:
        """根据 provider config 或全局 settings 构建 embedder。"""
        try:
            return build_embedding_client(
                api_key=self._config.get("embedding_api_key") or None,
                base_url=self._config.get("embedding_base_url") or None,
                model=self._config.get("embedding_model") or None,
            )
        except Exception as exc:
            logger.warning("Akasha embedder 构建失败", error=str(exc))
            return None

    async def is_available(self) -> bool:
        """检查 embedding 配置是否可用。"""
        embedder = self._build_embedder()
        if embedder is None:
            return False
        try:
            # 尝试一次实际调用验证连通性
            _ = await embedder.embed_one("test")
            return True
        except Exception as exc:
            logger.debug("Akasha is_available 测试失败", error=str(exc))
            return False
        finally:
            await embedder.close()

    async def initialize(self, session_id: str, **kwargs: Any) -> None:
        """初始化 engine。"""
        user_id = kwargs.get("user_id", "")
        if not user_id:
            return
        self._current_user_id = user_id
        from .engine import AkashaEngine

        embedder = self._build_embedder()
        self._engine = AkashaEngine(
            user_id=user_id,
            config=self._config,
            embedder=embedder,
        )
        logger.info("Akasha engine 初始化完成", user_id=user_id, db_path=str(self._engine.db_path))

    async def system_prompt_block(self, **kwargs: Any) -> str:
        """Akasha 不注入 L0，专注 L2 动态召回。"""
        return ""

    async def prefetch(self, query: str, *, session_id: str = "", **kwargs: Any) -> str:
        """执行图记忆检索，返回 Markdown 上下文。"""
        if self._engine is None:
            return ""
        try:
            result = await self._engine.query(session_id or self._current_user_id, query)
            return result.text
        except Exception as exc:
            logger.warning("Akasha prefetch 失败", error=str(exc))
            return ""

    async def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None:
        """把一轮对话同步到 Akasha 图。"""
        if self._engine is None or not session_id:
            return
        try:
            # 查询该会话最新的两条消息（user + assistant）
            user_msg = await self._get_latest_message(session_id, "user")
            assistant_msg = await self._get_latest_message(session_id, "assistant")
            if user_msg is None or assistant_msg is None:
                return

            # 计算 turn seq：按 conversation 内 created_at 排序
            seq = await self._compute_turn_seq(session_id, user_msg.created_at)

            await self._engine.commit_turn(
                session_key=session_id,
                user_msg=user_msg.content or "",
                assistant_msg=assistant_msg.content or "",
                user_msg_id=user_msg.message_id,
                assistant_msg_id=assistant_msg.message_id,
                seq=seq,
            )
            logger.debug(
                "Akasha sync_turn 完成",
                session_id=session_id,
                seq=seq,
            )
        except Exception as exc:
            logger.warning("Akasha sync_turn 失败", error=str(exc))

    async def _get_latest_message(self, conversation_id: str, role: str) -> Any | None:
        """查询指定 conversation 最新指定角色的消息。"""
        from sqlalchemy import select

        from core.db import get_async_session_maker
        from lib.chat.models import Message

        async with get_async_session_maker()() as db:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.role == role)
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def _compute_turn_seq(self, conversation_id: str, before_ts: Any) -> int:
        """计算某条消息在 conversation 内的 turn 序号。"""
        from sqlalchemy import func, select

        from core.db import get_async_session_maker
        from lib.chat.models import Message

        async with get_async_session_maker()() as db:
            stmt = select(func.count()).where(
                Message.conversation_id == conversation_id,
                Message.role == "user",
                Message.created_at <= before_ts,
            )
            result = await db.execute(stmt)
            return result.scalar() or 1

    async def get_tool_schemas(self) -> list[dict]:
        """暴露 akasha_recall 工具。"""
        return [
            {
                "name": "akasha_recall",
                "description": "从 Akasha 图记忆引擎召回相关历史对话。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "要召回的主题或问题",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "最多返回条数",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            }
        ]

    async def handle_tool_call(self, tool_name: str, args: dict, **kwargs: Any) -> str:
        """处理 akasha_recall 工具调用。"""
        if tool_name != "akasha_recall":
            return f'{{"error": "Tool {tool_name} not found"}}'
        query = args.get("query", "")
        session_id = kwargs.get("session_id", "")
        text = await self.prefetch(query, session_id=session_id)
        return text or "未找到相关记忆。"

    async def shutdown(self) -> None:
        if self._engine is not None:
            self._engine.close()
            self._engine = None

    async def get_config_schema(self) -> list[dict]:
        return [
            {
                "name": "db_path",
                "type": "string",
                "label": "Sidecar DB 路径",
                "description": "留空使用默认路径 ~/.lumen/memory/{user_id}/akasha.db",
            },
            {
                "name": "dense_top_k",
                "type": "integer",
                "label": "Dense Top K",
                "default": 10,
            },
            {
                "name": "ripple_top_k",
                "type": "integer",
                "label": "Ripple Top K",
                "default": 10,
            },
            {
                "name": "activate_limit",
                "type": "integer",
                "label": "Activate Limit",
                "default": 8,
            },
            {
                "name": "embedding_model",
                "type": "string",
                "label": "Embedding Model",
                "description": "覆盖全局 embedding_model",
            },
            {
                "name": "embedding_api_key",
                "type": "string",
                "label": "Embedding API Key",
                "description": "覆盖全局 embedding_api_key",
                "sensitive": True,
            },
            {
                "name": "embedding_base_url",
                "type": "string",
                "label": "Embedding Base URL",
                "description": "覆盖全局 embedding_base_url",
            },
        ]
