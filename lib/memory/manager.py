"""MemoryManager — fan-out 编排器。

进程级单例，维护内置 + 多个外部 provider，负责：
- 上下文组装（L0 冻结快照 + L2 动态召回）
- Provider 工具路由
- 生命周期钩子转发
- 写入镜像
- 断线重连（pending 队列 + 后台 reconcile）
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from lib.memory.builtin_provider import BuiltinMemoryProvider
from lib.memory.context_fence import build_memory_context_block, sanitize_context
from lib.memory.provider import MemoryProvider
from lib.metrics import record
from shared.logging import get_logger

logger = get_logger(__name__)

# 重连策略：初始 2 分钟，指数退避，封顶 30 分钟
_RECONCILE_INITIAL_S = 120
_RECONCILE_MAX_S = 1800
_RECONCILE_MULTIPLIER = 2
_RECONCILE_IDLE_S = 300  # 队列为空时等待 5 分钟


@dataclass
class PendingProvider:
    """待激活的 provider 配置。"""

    name: str
    provider_type: str
    config: dict[str, Any]
    provider: MemoryProvider
    retry_count: int = 0
    next_retry_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_error: str = ""

    def schedule_next_retry(self) -> None:
        """计算下一次重试时间（指数退避）。"""
        self.retry_count += 1
        delay = min(
            _RECONCILE_INITIAL_S * (_RECONCILE_MULTIPLIER ** (self.retry_count - 1)),
            _RECONCILE_MAX_S,
        )
        self.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)

    @property
    def is_due(self) -> bool:
        """是否到达重试时间。"""
        return self.next_retry_at <= datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 响应格式。"""
        return {
            "name": self.name,
            "provider_type": self.provider_type,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at.isoformat(),
            "last_error": self.last_error,
        }


class MemoryManager:
    """记忆管理器 — 进程级单例。

    Usage:
        manager = MemoryManager()
        manager.add_provider(BuiltinMemoryProvider())
        # 可注册多个外部 provider（用不同实例名区分）
        manager.add_provider(external_provider, instance_name="honcho-prod")

        # 会话启动时（按 chat_id 缓存）
        system_prompt = base_prompt + await manager.build_system_prompt(user_id="me")

        # 每轮动态上下文
        context = await manager.build_context(
            user_id="me",
            user_input="...",
            session_key="web:abc123",
        )
    """

    def __init__(self) -> None:
        self._providers: dict[str, MemoryProvider] = {}
        self._builtin: BuiltinMemoryProvider | None = None
        self._pending: list[PendingProvider] = []
        self._reconcile_task: asyncio.Task[None] | None = None
        # 内置 provider 自动注册
        self._register_builtin(BuiltinMemoryProvider())

    # ── Reconciler 生命周期 ──

    def start_reconciler(self) -> None:
        """启动后台 reconcile 循环。"""
        if self._reconcile_task is not None:
            return
        self._reconcile_task = asyncio.create_task(self._reconcile_loop())
        logger.info("Provider reconciler 已启动")

    def stop_reconciler(self) -> None:
        """停止后台 reconcile 循环。"""
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            self._reconcile_task = None
            logger.info("Provider reconciler 已停止")

    # ── Pending 队列管理 ──

    def add_pending(
        self,
        name: str,
        provider_type: str,
        config: dict[str, Any],
        provider: MemoryProvider,
        error: str = "",
    ) -> None:
        """将启动时失败的 provider 加入待激活队列。"""
        if any(p.name == name for p in self._pending):
            return
        self._pending.append(
            PendingProvider(
                name=name,
                provider_type=provider_type,
                config=config,
                provider=provider,
                last_error=error,
            )
        )
        logger.info("Provider 加入待激活队列", name=name, provider_type=provider_type)

    def get_pending_providers(self) -> list[dict[str, Any]]:
        """查询待激活队列状态。"""
        return [p.to_dict() for p in self._pending]

    async def reconcile_now(self) -> dict[str, Any]:
        """手动触发一次 reconcile，返回结果。"""
        return await self._try_activate_pending()

    async def _reconcile_loop(self) -> None:
        """后台循环：定期重试 pending providers。"""
        while True:
            try:
                if not self._pending:
                    await asyncio.sleep(_RECONCILE_IDLE_S)
                    continue

                # 找到最近的重试时间，等待或立即执行
                next_retry = min(p.next_retry_at for p in self._pending)
                now = datetime.now(UTC)

                if next_retry > now:
                    delay = min((next_retry - now).total_seconds(), 60)
                    await asyncio.sleep(delay)
                    continue

                await self._try_activate_pending()
                await asyncio.sleep(5)  # 短暂等待避免频繁重试

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("reconcile_loop 异常", error=str(exc))
                await asyncio.sleep(30)

    async def _try_activate_pending(self) -> dict[str, Any]:
        """尝试激活所有到期的 pending providers。"""
        due_items = [p for p in self._pending if p.is_due]
        activated: list[str] = []
        failed: list[dict[str, str]] = []

        for pending in due_items:
            try:
                if await pending.provider.is_available():
                    self.add_provider(pending.provider, instance_name=pending.name)
                    self._pending.remove(pending)
                    activated.append(pending.name)
                    logger.info(
                        "Provider 重连成功，已激活",
                        name=pending.name,
                        retry_count=pending.retry_count,
                    )
                else:
                    pending.schedule_next_retry()
            except Exception as exc:
                pending.last_error = str(exc)
                pending.schedule_next_retry()
                failed.append({"name": pending.name, "error": str(exc)})
                logger.debug(
                    "Provider 重连失败",
                    name=pending.name,
                    error=str(exc),
                    retry_count=pending.retry_count,
                )

        return {
            "activated": activated,
            "failed": failed,
            "pending_count": len(self._pending),
        }

    # ── Provider 管理 ──

    def _register_builtin(self, provider: BuiltinMemoryProvider) -> None:
        """注册内置 provider（类型安全）。"""
        self._builtin = provider
        self._providers["builtin"] = provider
        logger.info("注册内置 provider")

    def add_provider(self, provider: MemoryProvider, *, instance_name: str = "") -> None:
        """注册 provider。

        builtin 始终唯一；外部 provider 用 instance_name 区分，同名覆盖便于热重载。
        instance_name 为空时 fallback 到 provider.name。

        设计约束：最多 1 个外部 provider 并存（软限制）。
        本方法本身不强制此约束——约束在 startup（只启用第一个 enabled 配置）
        和 memory_providers API（create/update/reload 校验）层强制。
        本方法保留无限制注册能力，供单元测试和单实例热重载使用。
        """
        if provider.name == "builtin":
            assert isinstance(provider, BuiltinMemoryProvider)
            self._builtin = provider
            self._providers["builtin"] = provider
            logger.info("注册内置 provider")
            return

        key = instance_name or provider.name
        provider.instance_name = key

        # 同名覆盖（允许热重载）
        if key in self._providers:
            logger.info("覆盖已注册 provider", name=key, provider_type=provider.name)
        else:
            logger.info("注册外部 provider", name=key, provider_type=provider.name)

        self._providers[key] = provider

    def remove_provider(self, name: str) -> bool:
        """移除指定 provider，返回是否成功。"""
        if name == "builtin":
            logger.warning("不能移除内置 provider")
            return False
        if name not in self._providers:
            return False
        del self._providers[name]
        logger.info("移除 provider", name=name)
        return True

    def clear_external_providers(self) -> None:
        """移除所有外部 provider（保留 builtin）。"""
        external = [name for name in self._providers if name not in ("builtin", "noop")]
        for name in external:
            del self._providers[name]
        if external:
            logger.info("清空外部 providers", count=len(external))

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
                    blocks.append(f"[{provider.display_name}]\n{block}")
            except Exception as exc:
                logger.warning(
                    "system_prompt_block 失败",
                    provider=provider.display_name,
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
    ) -> str:
        """每轮动态上下文：外部 provider prefetch（L2）+ 当前时间。

        以 <memory-context> 围栏注入，追加在消息序列里。
        不包含 about_you.md（L0 已在冻结 system prompt 里）；
        L1 近期对话本就在消息历史中。
        """
        parts: list[str] = []

        # 当前日期时间（带时分，让模型感知时段；context_frame 每轮动态，不影响 system prompt prefix cache）
        now = datetime.now(UTC)
        parts.append(f"Current date/time: {now.strftime('%Y-%m-%d %H:%M')} UTC")

        # 外部 provider prefetch
        if user_input:
            prefetch_result = await self.prefetch_all(user_input, session_id=session_key, user_id=user_id)
            if prefetch_result and prefetch_result.strip():
                parts.append(prefetch_result)

        raw = "\n\n".join(parts)
        if not raw.strip():
            return ""

        # 清洗 + 围栏
        cleaned = sanitize_context(raw)
        return build_memory_context_block(cleaned)

    # ── Prefetch fan-out ──

    async def prefetch_all(self, query: str, *, session_id: str = "", user_id: str = "") -> str:
        """同时向所有 provider 发起 prefetch，结果按 [provider.display_name] 标注后拼接。

        一个 provider 失败只跳过它，不影响其他。
        """

        async def _fetch(provider: MemoryProvider) -> str:
            try:
                return await provider.prefetch(
                    query,
                    session_id=session_id,
                    user_id=user_id,  # type: ignore[call-arg]
                )
            except Exception as exc:
                logger.debug("prefetch 失败", provider=provider.display_name, error=str(exc))
                return ""

        fetched = await asyncio.gather(*(_fetch(p) for p in self._providers.values()))
        blocks: list[str] = []
        for provider, text in zip(self._providers.values(), fetched, strict=False):
            if text and text.strip():
                blocks.append(f"[{provider.display_name}]\n{text}")
        return "\n\n".join(blocks)

    async def queue_prefetch_all(self, query: str, *, session_id: str = "", user_id: str = "") -> None:
        """为下一回合排队后台预取。"""
        for provider in self._providers.values():
            try:
                await provider.queue_prefetch(query, session_id=session_id)
            except Exception as exc:
                logger.debug("queue_prefetch 失败", provider=provider.display_name, error=str(exc))

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
                logger.warning("sync_turn 失败", provider=provider.display_name, error=str(exc))

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
                    provider=provider.display_name,
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
                    provider=provider.display_name,
                    error=str(exc),
                )
        return f'{{"error": "Tool {tool_name} not found"}}'

    # ── 生命周期钩子 ──

    async def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        for provider in self._providers.values():
            try:
                await provider.on_turn_start(turn_number, message, **kwargs)
            except Exception as exc:
                logger.debug("on_turn_start 失败", provider=provider.display_name, error=str(exc))

    async def on_pre_compress(self, messages: list[dict]) -> str:
        results: list[str] = []
        for provider in self._providers.values():
            try:
                result = await provider.on_pre_compress(messages)
                if result and result.strip():
                    results.append(f"[{provider.display_name}]\n{result}")
            except Exception as exc:
                logger.debug(
                    "on_pre_compress 失败",
                    provider=provider.display_name,
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
                    provider=provider.display_name,
                    error=str(exc),
                )

    async def on_session_switch(self, new_session_id: str, *, reset: bool = False, **kwargs: Any) -> None:
        """转发 session 切换/重置到所有 provider。

        reset=True 时各 provider 应清除该 session 的外部状态。
        """
        for provider in self._providers.values():
            try:
                await provider.on_session_switch(new_session_id, reset=reset, **kwargs)
            except Exception as exc:
                logger.debug(
                    "on_session_switch 失败",
                    provider=provider.display_name,
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
        # metrics 埋点：统一在 on_memory_write funnel 计数，覆盖 memory_save + update_profile 两路
        # 绝不让观测拖垮业务：失败静默忽略
        with contextlib.suppress(Exception):
            await record("memory.writes", 1.0, labels={"target": target, "action": action})

        for name, provider in self._providers.items():
            if name == "builtin":
                continue
            try:
                await provider.on_memory_write(action, target, content, metadata)
            except Exception as exc:
                logger.debug(
                    "on_memory_write 失败",
                    provider=provider.display_name,
                    error=str(exc),
                )

    # ── 批量初始化和关闭 ──

    async def initialize_all(self, session_id: str, **kwargs: Any) -> None:
        for provider in self._providers.values():
            try:
                await provider.initialize(session_id, **kwargs)
            except Exception as exc:
                logger.warning("initialize 失败", provider=provider.display_name, error=str(exc))

    async def shutdown_all(self) -> None:
        self.stop_reconciler()
        for provider in self._providers.values():
            try:
                await provider.shutdown()
            except Exception as exc:
                logger.warning("shutdown 失败", provider=provider.display_name, error=str(exc))
