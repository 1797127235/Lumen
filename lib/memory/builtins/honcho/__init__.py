"""Honcho 记忆 Provider — 内置插件。

Honcho 通过 reasoning 模型从对话中提取结论（conclusions），
形成 peer representation，支持语义查询和上下文召回。

文档: https://honcho.dev/docs/v3/documentation/introduction/overview
"""

from __future__ import annotations

import os
import re
from typing import Any

from lib.memory.provider import MemoryProvider
from shared.logging import get_logger

logger = get_logger(__name__)


class Provider(MemoryProvider):
    """Honcho 外部记忆 Provider。

    - system_prompt_block(): 通过 peer.chat() 获取用户画像摘要
    - prefetch(): 通过 peer.chat() 或 representation() 语义召回
    - sync_turn(): 将每轮对话写入 Honcho session

    配置项（config 或环境变量）：
        api_key: HONCHO_API_KEY
        workspace_id: HONCHO_WORKSPACE_ID（默认 lumen）
        environment: HONCHO_ENVIRONMENT（默认 production）
    """

    def __init__(
        self,
        api_key: str | None = None,
        workspace_id: str | None = None,
        environment: str | None = None,
    ) -> None:
        super().__init__()
        self._client: Any | None = None
        self._api_key = api_key or os.getenv("HONCHO_API_KEY", "")
        self._workspace_id = workspace_id or os.getenv("HONCHO_WORKSPACE_ID", "lumen")
        self._environment = environment or os.getenv("HONCHO_ENVIRONMENT", "production")

    @property
    def name(self) -> str:
        return "honcho"

    def _get_client(self) -> Any:
        """延迟初始化 Honcho 客户端。"""
        if self._client is None:
            try:
                from honcho import Honcho
            except ImportError as exc:
                raise ImportError("honcho-ai SDK 未安装，请运行: pip install honcho-ai") from exc

            if not self._api_key:
                logger.warning("HONCHO_API_KEY 未设置，Honcho Provider 将不可用")

            self._client = Honcho(
                workspace_id=self._workspace_id,
                api_key=self._api_key or None,
                environment=self._environment,
            )
        return self._client

    # ── 核心生命周期 ──

    async def is_available(self) -> bool:
        """检查 Honcho 服务是否可用。"""
        try:
            client = self._get_client()
            client.workspaces()
            return True
        except Exception as exc:
            logger.debug("Honcho 连通性检查失败", error=str(exc))
            return False

    async def initialize(self, session_id: str, **kwargs: Any) -> None:
        """初始化：确保 user peer 和 session 存在。"""
        user_id = kwargs.get("user_id", "")
        if not user_id:
            return

        honcho_session_id = self._sanitize_session_id(session_id)

        try:
            client = self._get_client()
            peer = client.peer(user_id)
            session = client.session(honcho_session_id)
            session.add_peers([peer])
            logger.info(
                "Honcho 初始化完成",
                user_id=user_id,
                session_id=honcho_session_id,
                workspace=self._workspace_id,
            )
        except Exception as exc:
            logger.warning(
                "Honcho 初始化失败",
                user_id=user_id,
                session_id=honcho_session_id,
                error=str(exc),
            )

    # ── L0: 冻结快照 ──

    async def system_prompt_block(self, **kwargs: Any) -> str:
        """返回用户画像摘要，作为 L0 冻结快照注入 system prompt。"""
        user_id = kwargs.get("user_id", "")
        if not user_id:
            return ""

        try:
            client = self._get_client()
            peer = client.peer(user_id)
            response = peer.chat(
                "请用 2-3 句话总结这个用户的关键特征、偏好和重要背景。只输出客观事实，不编造。",
            )
            logger.debug(
                "Honcho system_prompt_block 成功",
                user_id=user_id,
                response_len=len(response),
            )
            return response
        except Exception as exc:
            logger.warning(
                "Honcho system_prompt_block 失败",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    # ── L1: 动态召回 ──

    async def prefetch(self, query: str, *, session_id: str = "", **kwargs: Any) -> str:
        """根据 query 从 Honcho 召回相关记忆上下文。"""
        user_id = kwargs.get("user_id", "")
        if not user_id or not query:
            return ""

        try:
            client = self._get_client()
            peer = client.peer(user_id)

            response = peer.chat(
                f"基于你对这个用户的了解，回答以下问题（简洁，100字内）：{query}",
            )
            logger.debug(
                "Honcho prefetch 成功",
                user_id=user_id,
                query_preview=query[:50],
                response_len=len(response),
            )
            return response
        except Exception as exc:
            logger.warning(
                "Honcho prefetch 失败",
                user_id=user_id,
                query_preview=query[:50],
                error=str(exc),
            )
            return ""

    # ── 轮次同步 ──

    def _sanitize_session_id(self, session_id: str) -> str:
        """清洗 session_id，将非法字符替换为连字符。

        Honcho 要求 ID 匹配 ^[a-zA-Z0-9_-]+$。
        """
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", session_id)
        sanitized = re.sub(r"-+", "-", sanitized).strip("-")
        return sanitized

    async def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None:
        """将对话轮次同步到 Honcho。"""
        if not session_id:
            return

        honcho_session_id = self._sanitize_session_id(session_id)

        try:
            client = self._get_client()
            session = client.session(honcho_session_id)

            peers = session.peers()
            user_peer = None
            assistant_peer = None

            for p in peers:
                if p.id != "lumen_assistant":
                    user_peer = p
                else:
                    assistant_peer = p

            if not user_peer:
                logger.debug(
                    "Honcho sync_turn: session 中没有 user peer",
                    session_id=honcho_session_id,
                    original_session_id=session_id,
                )
                return

            if not assistant_peer:
                assistant_peer = client.peer("lumen_assistant")
                session.add_peers([assistant_peer])
                logger.debug(
                    "Honcho sync_turn: 创建 assistant peer",
                    session_id=honcho_session_id,
                )

            session.add_messages(
                [
                    user_peer.message(user),
                    assistant_peer.message(assistant),
                ]
            )

            logger.info(
                "Honcho sync_turn 完成",
                session_id=honcho_session_id,
                user_peer=user_peer.id,
                user_msg_len=len(user),
                assistant_msg_len=len(assistant),
            )
        except Exception as exc:
            logger.warning(
                "Honcho sync_turn 失败",
                session_id=honcho_session_id,
                original_session_id=session_id,
                error=str(exc),
            )

    # ── 会话重置 ──

    async def on_session_switch(self, new_session_id: str, *, reset: bool = False, **kwargs: Any) -> None:
        """会话切换或重置时调用。

        reset=True 时删除对应 Honcho session 及其所有数据，
        确保清空对话后不会通过 prefetch 泄漏旧上下文。
        """
        if not reset:
            return

        old_session_id = kwargs.get("old_session_id") or new_session_id
        if not old_session_id:
            return

        honcho_session_id = self._sanitize_session_id(old_session_id)
        try:
            client = self._get_client()
            session = client.session(honcho_session_id)
            session.delete()
            logger.info("Honcho session 已删除（reset）", session_id=honcho_session_id)
        except Exception as exc:
            logger.warning(
                "Honcho session 删除失败",
                session_id=honcho_session_id,
                error=str(exc),
            )

    # ── 工具 ──

    async def get_tool_schemas(self) -> list[dict]:
        """Honcho Provider 目前不暴露额外工具。"""
        return []

    # ── 可选钩子 ──

    async def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """当 builtin 写入 MEMORY.md / USER.md 时，镜像到 Honcho。"""
        if action not in {"add", "replace", "remove"} or not content:
            return

        user_id = metadata.get("user_id", "") if metadata else ""
        if not user_id:
            return

        action_label = {"add": "新增", "replace": "更新", "remove": "删除"}.get(action, action)
        target_label = "用户画像" if target == "user" else "长期记忆"
        category = metadata.get("category", "fact") if metadata else "fact"

        try:
            client = self._get_client()
            peer = client.peer(user_id)
            memory_session = client.session(f"{user_id}_memory_ingest")
            memory_session.add_peers([peer])

            memory_session.add_messages(
                [
                    peer.message(f"[{action_label} {target_label}] [{category}] {content}"),
                ]
            )

            logger.info(
                "Honcho on_memory_write 完成",
                user_id=user_id,
                action=action,
                target=target,
                category=category,
                content_preview=content[:100],
                content_len=len(content),
            )
        except Exception as exc:
            logger.warning(
                "Honcho on_memory_write 失败",
                user_id=user_id,
                action=action,
                target=target,
                error=str(exc),
            )

    async def get_config_schema(self) -> list[dict]:
        """返回 UI 可配置字段。"""
        return [
            {
                "name": "api_key",
                "type": "string",
                "label": "Honcho API Key",
                "description": "Honcho AI API Key",
                "sensitive": True,
            },
            {
                "name": "workspace_id",
                "type": "string",
                "label": "Workspace ID",
                "description": "Honcho workspace ID",
                "default": "lumen",
            },
            {
                "name": "environment",
                "type": "string",
                "label": "Environment",
                "description": "Honcho environment",
                "default": "production",
            },
        ]

    async def get_stats(self, user_id: str) -> dict:
        """获取用户在 Honcho 的统计信息（用于监控）。"""
        try:
            client = self._get_client()
            peer = client.peer(user_id)
            sessions = peer.sessions()
            return {
                "conclusions": len(peer.conclusions()),
                "sessions": len(sessions),
            }
        except Exception as exc:
            logger.warning("Honcho get_stats 失败", user_id=user_id, error=str(exc))
            return {"error": str(exc)}
