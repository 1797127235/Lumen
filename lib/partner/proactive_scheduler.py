"""ProactiveScheduler — RSS 推送调度器

定时轮询 RSS → RSS Reader Agent → 频率控制 → 推送到 Telegram/Web

生命周期:
    1. startup.py 调用 start()
    2. 后台 asyncio.Task 运行 _loop
    3. shutdown 调用 stop()

调度流程 (每次 tick):
    1. rss_poll            → 拉取新内容
    2. rss_get_unread      → 获取未读条目
    3. 前置门控             → 频率限制 / 用户活跃检测 / 每日上限
    4. read_focus          → 读取 FOCUS.md（用户关注点）
    5. RSS Reader Agent    → 浏览 → 读原文 → 生成朋友式推送文案
    6. rss_acknowledge     → 标记已推送的条目为已读
    7. publish_outbound    → 推送到 Telegram
    8. 更新 presence       → 记录 last_proactive_at / proactive_sent_24h
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import get_async_session_maker
from lib.bus.queue import MessageBus, OutboundMessage
from lib.memory.markdown import AsyncMarkdownStore
from lib.tools.mcp.client_manager import get_mcp_manager
from shared.logging import get_logger

logger = get_logger(__name__)

# ── 常量 ──────────────────────────────────────────────

_MCP_SERVER = "lumen-rss"
_USER_ID = "demo_user"

# 活跃判定：用户最后消息时间在 COOLDOWN 内视为"正在聊天"
_ACTIVE_COOLDOWN = timedelta(minutes=10)

# 24 小时窗口起始时间
_DAY_WINDOW = timedelta(hours=24)


@dataclass
class _ProactiveState:
    """调度器运行时状态（非持久化）"""

    last_tick: datetime | None = None
    consecutive_empty: int = 0  # 连续空轮次数（无未读条目）


class ProactiveScheduler:
    """RSS 推送调度器"""

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._running = False
        self._task: asyncio.Task | None = None
        self._state = _ProactiveState()

    # ── 生命周期 ──────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="proactive-scheduler")
        logger.info("ProactiveScheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            import contextlib

            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("ProactiveScheduler stopped")

    # ── 主循环 ────────────────────────────────────────

    async def _loop(self) -> None:
        """后台 tick 循环"""
        # 启动后等待 30 秒，让 MCP 连接就绪
        await asyncio.sleep(30)

        # 首次 tick 立即执行，之后按配置间隔
        first_tick = True

        while self._running:
            if not first_tick:
                settings = get_settings()
                interval = self._calc_interval(settings)

                try:
                    await asyncio.sleep(interval.total_seconds())
                except asyncio.CancelledError:
                    break

                if not self._running:
                    break

            try:
                await self._tick()
            except Exception:
                logger.exception("ProactiveScheduler tick error")
            finally:
                first_tick = False

    def _calc_interval(self, settings: Any) -> timedelta:
        """根据用户活跃度计算下次 tick 间隔

        用户活跃（10 分钟内有消息）→ 长间隔（避免打扰）
        用户不活跃                → 短间隔（及时推送）
        连续空轮                  → 逐步拉长（避免无效轮询）
        """
        base_min = getattr(settings, "proactive_interval_min", None) or 120
        base_max = getattr(settings, "proactive_interval_max", None) or 480

        # 连续空轮时逐步拉长间隔
        backoff_factor = min(self._state.consecutive_empty, 5)
        min_interval = base_min + backoff_factor * 5  # 每次空轮 +5 分钟

        return timedelta(seconds=min(base_max, min_interval) * 60)

    # ── 单次 tick ─────────────────────────────────────

    async def _tick(self) -> None:
        """执行一次完整的推送检查"""
        now = datetime.now(UTC)
        # 重新加载 config.json，确保自动保存的 telegram_chat_id 等配置生效
        from core.config import apply_user_config, load_user_config

        apply_user_config(get_settings(), load_user_config())
        settings = get_settings()
        mcp = get_mcp_manager()

        # 1. 检查 RSS MCP 是否连接
        status = mcp.get_status()
        rss_connected = any(s.name == _MCP_SERVER and s.state == "connected" for s in status)
        if not rss_connected:
            logger.debug("RSS MCP 未连接，跳过 tick")
            return

        # 2. 拉取新 RSS 内容
        poll_result = await mcp.call_tool(_MCP_SERVER, "rss_poll", {})
        poll_text = _unwrap_tool(poll_result)
        if _is_error(poll_text):
            logger.warning("rss_poll 失败", result=poll_text[:200])
            return

        # 3. 获取未读条目
        unread_result = await mcp.call_tool(_MCP_SERVER, "rss_get_unread", {"limit": 20})
        unread_text = _unwrap_tool(unread_result)
        if _is_error(unread_text):
            logger.warning("rss_get_unread 失败", result=unread_text[:200])
            return

        entries = _parse_entries(unread_text)[:50]  # 最多处理 50 条，防止 token 爆炸
        if not entries:
            self._state.consecutive_empty += 1
            return

        self._state.consecutive_empty = 0

        # 4. 前置门控
        async with get_async_session_maker()() as db:
            user_id = await self._get_target_user(db)
            gate = await self._check_gates(db, settings, now, user_id)
            if not gate.passed:
                logger.debug("门控未通过，跳过推送", reason=gate.reason)
                return

            # 5. 读取 FOCUS.md
            store = AsyncMarkdownStore()
            focus = await store.read_focus(user_id)

            # 6. 调用 RSS Reader Agent 生成推送文案
            from lib.partner.rss_reader import get_rss_reader

            reader = get_rss_reader()
            message = await reader.generate_push(entries, focus, user_id)
            if not message:
                logger.debug("RSS Reader Agent 无推荐")
                return

            # 7. 从文案中提取 URL，匹配条目并 ack
            urls_in_message = re.findall(r"https?://[^\s)\]]+", message)
            ack_ids = []
            for e in entries:
                url = e.get("url", e.get("link", ""))
                if url:
                    url_norm = url.rstrip("/")
                    for msg_url in urls_in_message:
                        if url_norm == msg_url.rstrip("/"):
                            eid = e.get("event_id", e.get("id", ""))
                            if eid:
                                ack_ids.append(eid)
                            break

            if not ack_ids:
                logger.warning(
                    "RSS Reader 生成的文案没有匹配到任何条目 URL，跳过推送防止重复",
                    user_id=user_id,
                )
                return

            try:
                await mcp.call_tool(_MCP_SERVER, "rss_acknowledge", {"event_ids": ack_ids})
            except Exception:
                logger.exception("rss_acknowledge 失败，跳过推送防止重复")
                return

            # 8. 推送
            chat_id = settings.telegram_chat_id
            if not chat_id:
                logger.debug("无 telegram_chat_id，跳过推送")
                return

            await self._bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id=chat_id,
                    content=message,
                )
            )

            # 9. 更新 presence
            await self._update_presence(db, now, user_id)
            await db.commit()

        self._state.last_tick = now
        logger.info("推送完成", message_len=len(message))

    # ── 门控检查 ──────────────────────────────────────

    @dataclass
    class _GateResult:
        passed: bool
        reason: str = ""

    async def _check_gates(self, db: AsyncSession, settings: Any, now: datetime, user_id: str) -> _GateResult:
        """频率限制 / 用户活跃 / 每日上限"""
        # 检查 1: 该用户正在聊天（10 分钟内有消息）
        result = await db.execute(
            text("SELECT last_user_at FROM lumen_presence WHERE user_id = :uid"),
            {"uid": user_id},
        )
        row = result.first()
        last_user_at = _parse_db_datetime(row[0]) if row else None

        if last_user_at and (now - last_user_at) < _ACTIVE_COOLDOWN:
            return self._GateResult(passed=False, reason="用户正在聊天中")

        # 检查 2: 每日上限
        daily_limit = getattr(settings, "proactive_daily_limit", None) or 20
        result = await db.execute(
            text("SELECT proactive_sent_24h, last_proactive_at FROM lumen_presence WHERE user_id = :uid"),
            {"uid": user_id},
        )
        row = result.first()
        last_proactive: datetime | None = None
        if row:
            sent_24h = row[0] or 0
            last_proactive = _parse_db_datetime(row[1])
            # 如果上次推送是 24h 前，重置计数（写回数据库）
            if last_proactive and (now - last_proactive) > _DAY_WINDOW:
                if sent_24h > 0:
                    await db.execute(
                        text("UPDATE lumen_presence SET proactive_sent_24h = 0 WHERE user_id = :uid"),
                        {"uid": _USER_ID},
                    )
                sent_24h = 0
            if sent_24h >= daily_limit:
                return self._GateResult(passed=False, reason=f"已达每日上限 ({sent_24h}/{daily_limit})")

        # 检查 3: 最短推送间隔（至少 15 分钟）
        if last_proactive and (now - last_proactive) < timedelta(minutes=15):
            return self._GateResult(passed=False, reason="距上次推送不足 15 分钟")

        return self._GateResult(passed=True)

    async def _get_target_user(self, db: AsyncSession) -> str | None:
        """获取推送目标用户。当前单用户模式：优先返回 lumen_presence 中存在的用户，否则 demo_user。"""
        try:
            result = await db.execute(text("SELECT user_id FROM lumen_presence LIMIT 1"))
            row = result.first()
            if row and row[0]:
                return str(row[0])
        except Exception:
            logger.debug("读取 lumen_presence 用户失败，使用默认用户")
        return _USER_ID

    # ── Presence 更新 ─────────────────────────────────

    async def _update_presence(self, db: AsyncSession, now: datetime, user_id: str) -> None:
        """更新推送统计"""
        try:
            await db.execute(
                text("""
                INSERT INTO lumen_presence (user_id, last_user_at, last_proactive_at, proactive_sent_24h, updated_at)
                VALUES (:uid, :now, :now, 1, :now)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_proactive_at = :now,
                    proactive_sent_24h = MIN(COALESCE(proactive_sent_24h, 0) + 1, 99),
                    updated_at = :now
            """),
                {"uid": user_id, "now": now},
            )
        except Exception as e:
            logger.warning("presence 更新失败", error=str(e))


# ── 工具函数 ──────────────────────────────────────────


def _parse_db_datetime(val: Any) -> datetime | None:
    """将 SQLite 返回的 datetime 值（可能是 str 或 naive datetime）转为 aware UTC datetime"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            logger.warning("无法解析 datetime 字符串", value=val)
            return None
    return None


def _unwrap_tool(result: str) -> str:
    """从 MCP call_tool 结果中提取文本"""
    return str(result) if result else ""


def _is_error(text: str) -> bool:
    """检查工具返回文本是否为错误"""
    if not text:
        return True
    return text.strip().startswith("❌") or "error" in text[:50].lower()


def _parse_entries(text: str) -> list[dict]:
    """解析 rss_get_unread 的返回结果为条目列表

    lumen-rss MCP 返回 JSON 字符串或格式化文本。
    尝试 JSON 解析，失败则返回空列表。
    """
    if not text or _is_error(text):
        return []

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # 可能在 "entries" / "items" / "results" 键下
            for key in ("entries", "items", "results", "data"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []
    except json.JSONDecodeError:
        return []
