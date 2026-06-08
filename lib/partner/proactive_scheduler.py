"""ProactiveScheduler — RSS 推送调度器

定时轮询 RSS → FOCUS.md LLM 过滤 → 频率控制 → 推送到 Telegram/Web

生命周期:
    1. startup.py 调用 start()
    2. 后台 asyncio.Task 运行 _loop
    3. shutdown 调用 stop()

调度流程 (每次 tick):
    1. rss_poll          → 拉取新内容
    2. rss_get_unread    → 获取未读条目
    3. 前置门控           → 频率限制 / 用户活跃检测 / 每日上限
    4. read_focus        → 读取 FOCUS.md（用户关注点）
    5. LLM 过滤          → 从未读条目中挑选值得推送的
    6. publish_outbound  → 推送到 Telegram
    7. rss_acknowledge   → 标记已推送的条目为已读
    8. 更新 presence     → 记录 last_proactive_at / proactive_sent_24h
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic_ai import ToolReturn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import build_llm_call_params, get_settings
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
        min_interval = base_min + backoff_factor * 30  # 每次空轮 +30s

        return timedelta(seconds=min(base_max, min_interval) * 60)

    # ── 单次 tick ─────────────────────────────────────

    async def _tick(self) -> None:
        """执行一次完整的推送检查"""
        now = datetime.now(UTC)
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

        entries = _parse_entries(unread_text)
        if not entries:
            self._state.consecutive_empty += 1
            return

        self._state.consecutive_empty = 0

        # 4. 前置门控
        async with get_async_session_maker()() as db:
            gate = await self._check_gates(db, settings, now)
            if not gate.passed:
                logger.debug("门控未通过，跳过推送", reason=gate.reason)
                return

            # 5. 读取 FOCUS.md
            store = AsyncMarkdownStore()
            focus = await store.read_focus(_USER_ID)

            # 6. LLM 过滤
            recommended = await self._llm_filter(entries, focus)
            if not recommended:
                logger.debug("LLM 过滤后无推荐条目")
                return

            # 7. 推送
            message = self._format_push_message(recommended)
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

            # 8. 标记已读（MCP rss_acknowledge 接受 event_ids 列表）
            ack_ids = [eid for e in recommended if (eid := e.get("event_id", e.get("id", "")))]
            if ack_ids:
                await mcp.call_tool(_MCP_SERVER, "rss_acknowledge", {"event_ids": ack_ids})

            # 9. 更新 presence
            await self._update_presence(db, now, len(recommended))
            await db.commit()

        self._state.last_tick = now
        logger.info("推送完成", count=len(recommended))

    # ── 门控检查 ──────────────────────────────────────

    @dataclass
    class _GateResult:
        passed: bool
        reason: str = ""

    async def _check_gates(self, db: AsyncSession, settings: Any, now: datetime) -> _GateResult:
        """频率限制 / 用户活跃 / 每日上限"""
        # 检查 1: 用户正在聊天（10 分钟内有消息）
        result = await db.execute(
            text("SELECT last_user_at FROM lumen_presence WHERE user_id = :uid"),
            {"uid": _USER_ID},
        )
        row = result.first()
        last_user_at = _parse_db_datetime(row[0]) if row else None

        if last_user_at and (now - last_user_at) < _ACTIVE_COOLDOWN:
            return self._GateResult(passed=False, reason="用户正在聊天中")

        # 检查 2: 每日上限
        daily_limit = getattr(settings, "proactive_daily_limit", None) or 8
        result = await db.execute(
            text("SELECT proactive_sent_24h, last_proactive_at FROM lumen_presence WHERE user_id = :uid"),
            {"uid": _USER_ID},
        )
        row = result.first()
        last_proactive: datetime | None = None
        if row:
            sent_24h = row[0] or 0
            last_proactive = _parse_db_datetime(row[1])
            # 如果上次推送是 24h 前，重置计数
            if last_proactive and (now - last_proactive) > _DAY_WINDOW:
                sent_24h = 0
            if sent_24h >= daily_limit:
                return self._GateResult(passed=False, reason=f"已达每日上限 ({sent_24h}/{daily_limit})")

        # 检查 3: 最短推送间隔（至少 15 分钟）
        if last_proactive and (now - last_proactive) < timedelta(minutes=15):
            return self._GateResult(passed=False, reason="距上次推送不足 15 分钟")

        return self._GateResult(passed=True)

    # ── LLM 过滤 ─────────────────────────────────────

    async def _llm_filter(self, entries: list[dict], focus: str) -> list[dict]:
        """调用 LLM 从未读条目中挑选值得推送的

        输入: 未读条目列表 + FOCUS.md（用户关注点）
        输出: 推荐推送的条目列表（含推荐语）
        """
        if not entries:
            return []

        # 构建条目摘要（兼容 MCP 字段名: url/link, event_id/id）
        items_text = "\n\n".join(
            f"[{i}] {e.get('title', '无标题')}\n"
            f"来源: {e.get('source_name', e.get('feed_title', e.get('feed', '未知')))}\n"
            f"摘要: {e.get('content', e.get('summary', e.get('description', '无摘要')))[:200]}\n"
            f"链接: {e.get('url', e.get('link', ''))}\n"
            f"ID: {e.get('event_id', e.get('id', ''))}"
            for i, e in enumerate(entries)
        )

        focus_section = (
            f"\n## 用户关注点（FOCUS.md）\n{focus}" if focus else "\n## 用户关注点\n（暂无，按通用兴趣推荐）"
        )

        prompt = f"""你是一个内容筛选助手。下面是一些未读的 RSS 条目，请从中挑选最值得推荐给用户的。

{focus_section}

## 未读条目
{items_text}

## 要求
1. 只推荐与用户关注点相关的条目（没有关注点时推荐技术/AI 相关的高质量内容）
2. 为每条推荐写一句简短的推荐语（20 字以内，说明为什么推荐）
3. 如果没有值得推荐的，返回空数组

## 输出格式（严格 JSON）
```json
[
  {{
    "index": 0,
    "reason": "推荐语"
  }}
]
```"""

        try:
            import litellm

            # 使用 rss_filter_model 或默认模型
            settings = get_settings()
            model_name = getattr(settings, "rss_filter_model", "") or None
            llm_params = build_llm_call_params(model=model_name)

            kwargs: dict[str, Any] = {
                "model": llm_params["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 512,
                "api_key": llm_params["api_key"],
                "stream": False,
                "timeout": 30,
            }
            if llm_params["base_url"]:
                kwargs["base_url"] = llm_params["base_url"]

            response = await litellm.acompletion(**kwargs)
            response = cast(Any, response)
            content = response.choices[0].message.content or ""

            # 解析 JSON
            picks = _extract_json_array(content)
            if not picks:
                return []

            # 映射回原始条目，标准化字段名
            recommended = []
            for pick in picks:
                idx = pick.get("index")
                if isinstance(idx, int) and 0 <= idx < len(entries):
                    entry = {
                        **entries[idx],
                        "_reason": pick.get("reason", ""),
                        # 标准化字段名，兼容 MCP (url/event_id) 和旧字段 (link/id)
                        "link": entries[idx].get("url", entries[idx].get("link", "")),
                        "id": entries[idx].get("event_id", entries[idx].get("id", "")),
                    }
                    recommended.append(entry)

            return recommended

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LLM 过滤失败")
            return []

    # ── 消息格式化 ────────────────────────────────────

    def _format_push_message(self, entries: list[dict]) -> str:
        """将推荐条目格式化为推送消息"""
        if len(entries) == 1:
            e = entries[0]
            reason = e.get("_reason", "")
            parts = [f"📰 {e.get('title', '无标题')}"]
            if reason:
                parts.append(f"💡 {reason}")
            link = e.get("link", "")
            if link:
                parts.append(f"🔗 {link}")
            return "\n".join(parts)

        # 多条推荐
        lines = ["📰 你关注的领域有更新：\n"]
        for e in entries:
            title = e.get("title", "无标题")
            reason = e.get("_reason", "")
            feed = e.get("feed_title", e.get("feed", ""))
            link = e.get("link", "")
            line = f"**{title}**"
            if feed:
                line += f" — {feed}"
            if reason:
                line += f"\n  💡 {reason}"
            if link:
                line += f"\n  🔗 {link}"
            lines.append(line)
            lines.append("")

        return "\n".join(lines)

    # ── Presence 更新 ─────────────────────────────────

    async def _update_presence(self, db: AsyncSession, now: datetime, count: int) -> None:
        """更新推送统计"""
        try:
            await db.execute(
                text("""
                INSERT INTO lumen_presence (user_id, last_user_at, last_proactive_at, proactive_sent_24h, updated_at)
                VALUES (:uid, :now, :now, :count, :now)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_proactive_at = :now,
                    proactive_sent_24h = MIN(proactive_sent_24h + :count, 99),
                    updated_at = :now
            """),
                {"uid": _USER_ID, "now": now, "count": count},
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
        dt = datetime.fromisoformat(val)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def _unwrap_tool(result: str | ToolReturn) -> str:
    """从 MCP call_tool 结果中提取文本"""
    if isinstance(result, ToolReturn):
        return str(result.return_value) if result.return_value else ""
    return str(result)


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


def _extract_json_array(text: str) -> list[dict]:
    """从 LLM 响应中提取 JSON 数组"""
    # 尝试直接解析
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取
    import re

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # 尝试找到第一个 [ ... ] 块
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    return []
