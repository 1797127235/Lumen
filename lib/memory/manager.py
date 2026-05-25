"""MemoryManager — fan-out 编排器。

进程级单例，维护内置 + 最多一个外部 provider，负责：
- 上下文组装（L0 冻结快照 + L2 动态召回）
- Provider 工具路由
- 生命周期钩子转发
- 写入镜像
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lib.memory.builtin_provider import BuiltinMemoryProvider
from lib.memory.context_fence import build_memory_context_block, sanitize_context
from lib.memory.provider import MemoryProvider
from shared.logging import get_logger

logger = get_logger(__name__)


class MemoryManager:
    """记忆管理器 — 进程级单例。

    Usage:
        manager = MemoryManager()
        manager.add_provider(BuiltinMemoryProvider())
        # 可选：manager.add_provider(external_provider)

        # 会话启动时（按 chat_id 缓存）
        system_prompt = base_prompt + await manager.build_system_prompt(user_id="demo_user")

        # 每轮动态上下文
        context = await manager.build_context(
            user_id="demo_user",
            user_input="...",
            session_key="web:abc123",
        )
    """

    def __init__(self) -> None:
        self._providers: dict[str, MemoryProvider] = {}
        self._builtin: BuiltinMemoryProvider | None = None
        # 内置 provider 自动注册
        self._register_builtin(BuiltinMemoryProvider())

    # ── Provider 管理 ──

    def _register_builtin(self, provider: BuiltinMemoryProvider) -> None:
        """注册内置 provider（类型安全）。"""
        self._builtin = provider
        self._providers["builtin"] = provider
        logger.info("注册内置 provider")

    def add_provider(self, provider: MemoryProvider) -> None:
        """注册 provider。

        builtin 始终允许；外部 provider 最多一个。
        """
        if provider.name == "builtin":
            assert isinstance(provider, BuiltinMemoryProvider)
            self._builtin = provider
            self._providers["builtin"] = provider
            logger.info("注册内置 provider")
            return

        # 检查是否已存在外部 provider
        existing_external = [name for name in self._providers if name not in ("builtin", "noop")]
        if existing_external:
            logger.warning(
                "拒绝注册第二个外部 provider",
                rejected=provider.name,
                existing=existing_external[0],
            )
            return

        self._providers[provider.name] = provider
        logger.info("注册外部 provider", name=provider.name)

    def get_provider(self, name: str) -> MemoryProvider | None:
        return self._providers.get(name)

    @property
    def providers(self) -> list[MemoryProvider]:
        return list(self._providers.values())

    # ── 系统提示词（L0 冻结快照） ──

    async def build_system_prompt(self, user_id: str = "", **kwargs: Any) -> str:
        """汇总各 provider 的 system_prompt_block()，在会话启动时取一次。

        builtin 返回 about_you.md(+memory.md) 冻结快照；
        外部 provider 返回自我介绍。
        """
        blocks: list[str] = []
        for provider in self._providers.values():
            try:
                block = await provider.system_prompt_block(
                    user_id=user_id,
                    **kwargs,  # type: ignore[call-arg]
                )
                if block and block.strip():
                    blocks.append(f"[{provider.name}]\n{block}")
            except Exception as exc:
                logger.warning(
                    "system_prompt_block 失败",
                    provider=provider.name,
                    error=str(exc),
                )
        return "\n\n".join(blocks)

    # ── 动态上下文（L2） ──

    async def build_context(
        self,
        user_id: str,
        user_input: str,
        *,
        session_key: str = "",
        conversation_summary: str = "",
    ) -> str:
        """每轮动态上下文：外部 provider prefetch（L2）+ 当前时间。

        以 <memory-context> 围栏注入，追加在消息序列里。
        不包含 about_you.md（L0 已在冻结 system prompt 里）；
        L1 近期对话本就在消息历史中。
        """
        parts: list[str] = []

        # 当前时间
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"Current time: {now}")

        # 外部 provider prefetch
        if user_input:
            prefetch_result = await self.prefetch_all(user_input, session_id=session_key, user_id=user_id)
            if prefetch_result and prefetch_result.strip():
                parts.append(prefetch_result)

        if conversation_summary:
            parts.append(f"Conversation summary: {conversation_summary}")

        raw = "\n\n".join(parts)
        if not raw.strip():
            return ""

        # 清洗 + 围栏
        cleaned = sanitize_context(raw)
        return build_memory_context_block(cleaned)

    # ── Prefetch fan-out ──

    async def prefetch_all(self, query: str, *, session_id: str = "", user_id: str = "") -> str:
        """同时向所有 provider 发起 prefetch，结果按 [provider.name] 标注后拼接。

        一个 provider 失败只跳过它，不影响其他。
        """
        results: list[str] = []
        for provider in self._providers.values():
            try:
                result = await provider.prefetch(
                    query,
                    session_id=session_id,
                    user_id=user_id,  # type: ignore[call-arg]
                )
                if result and result.strip():
                    results.append(f"[{provider.name}]\n{result}")
            except Exception as exc:
                logger.debug("prefetch 失败", provider=provider.name, error=str(exc))
        return "\n\n".join(results)

    async def queue_prefetch_all(self, query: str, *, session_id: str = "", user_id: str = "") -> None:
        """为下一回合排队后台预取。"""
        for provider in self._providers.values():
            try:
                await provider.queue_prefetch(query, session_id=session_id)
            except Exception as exc:
                logger.debug("queue_prefetch 失败", provider=provider.name, error=str(exc))

    # ── 轮次同步 ──

    async def sync_all(
        self,
        user_msg: str,
        assistant_msg: str,
        *,
        session_id: str = "",
    ) -> None:
        """将对话轮次广播到所有 provider。

        builtin 的 sync_turn 是空操作；外部 provider 自行决定是否同步。
        """
        for provider in self._providers.values():
            try:
                await provider.sync_turn(user_msg, assistant_msg, session_id=session_id)
            except Exception as exc:
                logger.warning("sync_turn 失败", provider=provider.name, error=str(exc))

    # ── 工具路由 ──

    async def get_all_tool_schemas(self) -> list[dict]:
        """汇总所有 provider 的工具 schema。"""
        schemas: list[dict] = []
        for provider in self._providers.values():
            try:
                provider_schemas = await provider.get_tool_schemas()
                schemas.extend(provider_schemas)
            except Exception as exc:
                logger.warning(
                    "get_tool_schemas 失败",
                    provider=provider.name,
                    error=str(exc),
                )
        return schemas

    async def has_tool(self, tool_name: str) -> bool:
        """检查是否有 provider 声明了该工具。"""
        for provider in self._providers.values():
            try:
                schemas = await provider.get_tool_schemas()
                for schema in schemas:
                    if schema.get("name") == tool_name:
                        return True
            except Exception:
                continue
        return False

    async def handle_tool_call(self, tool_name: str, args: dict, **kwargs: Any) -> str:
        """路由工具调用到对应 provider。"""
        for provider in self._providers.values():
            try:
                schemas = await provider.get_tool_schemas()
                schema_names = {s.get("name") for s in schemas}
                if tool_name in schema_names:
                    return await provider.handle_tool_call(tool_name, args, **kwargs)
            except Exception as exc:
                logger.warning(
                    "handle_tool_call 检查失败",
                    provider=provider.name,
                    error=str(exc),
                )
        return f'{{"error": "Tool {tool_name} not found"}}'

    # ── 生命周期钩子 ──

    async def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        for provider in self._providers.values():
            try:
                await provider.on_turn_start(turn_number, message, **kwargs)
            except Exception as exc:
                logger.debug("on_turn_start 失败", provider=provider.name, error=str(exc))

    async def on_pre_compress(self, messages: list[dict]) -> str:
        results: list[str] = []
        for provider in self._providers.values():
            try:
                result = await provider.on_pre_compress(messages)
                if result and result.strip():
                    results.append(f"[{provider.name}]\n{result}")
            except Exception as exc:
                logger.debug(
                    "on_pre_compress 失败",
                    provider=provider.name,
                    error=str(exc),
                )
        return "\n\n".join(results)

    async def on_session_end(self, messages: list[dict]) -> None:
        for provider in self._providers.values():
            try:
                await provider.on_session_end(messages)
            except Exception as exc:
                logger.debug(
                    "on_session_end 失败",
                    provider=provider.name,
                    error=str(exc),
                )

    # ── 写入镜像 ──

    async def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """builtin 写入时转发给所有外部 provider（跳过 builtin 自身）。"""
        for name, provider in self._providers.items():
            if name == "builtin":
                continue
            try:
                await provider.on_memory_write(action, target, content, metadata)
            except Exception as exc:
                logger.debug(
                    "on_memory_write 失败",
                    provider=provider.name,
                    error=str(exc),
                )

    # ── 批量初始化和关闭 ──

    async def initialize_all(self, session_id: str, **kwargs: Any) -> None:
        for provider in self._providers.values():
            try:
                await provider.initialize(session_id, **kwargs)
            except Exception as exc:
                logger.warning("initialize 失败", provider=provider.name, error=str(exc))

    async def shutdown_all(self) -> None:
        for provider in self._providers.values():
            try:
                await provider.shutdown()
            except Exception as exc:
                logger.warning("shutdown 失败", provider=provider.name, error=str(exc))
