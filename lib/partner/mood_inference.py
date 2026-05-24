"""情绪状态推断 — 基于互动元数据，不读对话内容"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.logging import get_logger

logger = get_logger(__name__)

MoodType = Literal["calm", "curious", "tender", "reflective", "energized"]

# 推断规则（优先级从高到低）：
# energized:  消息轮次 >= 10 且 间隔 <= 5 分钟
# curious:    存在 "discovery" / "question" 类型的 GrowthEvent >= 1 条（近 5 次对话）
# tender:     距上次对话 >= 2 天（重逢），或用户轮次少（<= 2）但对话间隔短
# reflective: 消息轮次在 4-9 之间，且 GrowthEvent 包含 "reflection"/"struggle" 类型
# calm:       默认，以上都不满足


async def _get_interaction_metadata(db: AsyncSession, user_id: str) -> dict:
    """从最近 5 次对话读取互动元数据（不读消息文本）"""
    try:
        # 最近 5 次对话的消息数、时间间隔
        result = await db.execute(
            text("""
            SELECT message_count, last_message_at, created_at
            FROM conversations
            WHERE user_id = :user_id
            ORDER BY last_message_at DESC
            LIMIT 5
        """),
            {"user_id": user_id},
        )
        rows = result.fetchall()

        if not rows:
            return {}

        msg_count = rows[0][0] or 0
        last_msg_at = rows[0][1]
        now = datetime.now(UTC)

        # 间隔分钟数（与最近一次对话）
        gap_minutes = 0
        if last_msg_at:
            if isinstance(last_msg_at, str):
                from datetime import datetime as dt

                last_msg_at = dt.fromisoformat(last_msg_at.replace("Z", "+00:00"))
            if last_msg_at.tzinfo is None:
                last_msg_at = last_msg_at.replace(tzinfo=UTC)
            gap_minutes = (now - last_msg_at).total_seconds() / 60

        # 距上次对话的天数（取最近两条对话的时间差）
        days_since_last = gap_minutes / 1440  # 转换为天

        # 近 5 次对话的 GrowthEvent 类型分布
        ge_result = await db.execute(
            text("""
            SELECT event_type FROM growth_events
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 20
        """),
            {"user_id": user_id},
        )
        event_types = [r[0] for r in ge_result.fetchall()]

        return {
            "msg_count": msg_count,
            "gap_minutes": gap_minutes,
            "days_since_last": days_since_last,
            "event_types": event_types,
        }
    except Exception as e:
        logger.warning("互动元数据读取失败", error=str(e))
        return {}


def _infer_mood(metadata: dict) -> tuple[MoodType, float, list[str]]:
    """
    返回 (mood, intensity, derived_from_items)
    derived_from_items 是调试用字符串列表
    """
    if not metadata:
        return "calm", 0.3, ["no_data:cold_start"]

    msg_count: int = metadata.get("msg_count", 0)
    gap_minutes: float = metadata.get("gap_minutes", 0)
    days_since_last: float = metadata.get("days_since_last", 0)
    event_types: list[str] = metadata.get("event_types", [])

    derived: list[str] = [
        f"msg_count:{msg_count}",
        f"gap_min:{round(gap_minutes, 1)}",
        f"days_since:{round(days_since_last, 1)}",
    ]

    # energized: 高频快节奏
    if msg_count >= 10 and gap_minutes <= 5:
        derived.append("inferred:energized")
        return "energized", 0.8, derived

    # tender: 重逢（2天以上没聊）
    if days_since_last >= 2:
        derived.append("inferred:tender(reunion)")
        return "tender", 0.7, derived

    # curious: 有探索性/发现类事件
    discovery_events = [
        et for et in event_types if any(k in et.lower() for k in ("discovery", "question", "learn", "explore", "新"))
    ]
    if len(discovery_events) >= 1:
        derived.append(f"inferred:curious(events:{len(discovery_events)})")
        return "curious", 0.6, derived

    # reflective: 中等深度对话，包含反思类事件
    reflective_events = [
        et for et in event_types if any(k in et.lower() for k in ("reflection", "struggle", "concern", "困", "迷"))
    ]
    if 4 <= msg_count <= 9 or len(reflective_events) >= 1:
        derived.append(f"inferred:reflective(msg:{msg_count},events:{len(reflective_events)})")
        return "reflective", 0.5, derived

    derived.append("inferred:calm(default)")
    return "calm", 0.4, derived


async def update_mood_state(db_session_factory, user_id: str) -> None:
    """
    对话结束后异步调用。
    读取互动元数据 → 推断新情绪 → 更新 lumen_state（带切换防抖：连续 2 次相同新情绪才切换）
    """
    from core.db import get_async_session_maker
    from lib.partner.models import LumenState

    try:
        async with get_async_session_maker()() as db:
            # 读取元数据
            metadata = await _get_interaction_metadata(db, user_id)
            new_mood, intensity, derived_items = _infer_mood(metadata)
            derived_json = json.dumps(derived_items, ensure_ascii=False)

            # 读取当前状态
            state = await db.get(LumenState, user_id)
            if state is None:
                state = LumenState(user_id=user_id)
                db.add(state)

            current_mood = state.mood

            if new_mood == current_mood:
                # 情绪未变，重置候选
                state.pending_mood = None
                state.pending_count = 0
            elif new_mood == state.pending_mood:
                # 候选情绪连续出现
                state.pending_count = (state.pending_count or 0) + 1
                if state.pending_count >= 2:
                    # 切换
                    state.mood = new_mood
                    state.mood_intensity = intensity
                    state.pending_mood = None
                    state.pending_count = 0
                    logger.info("情绪切换", user_id=user_id, old=current_mood, new=new_mood)
            else:
                # 新候选
                state.pending_mood = new_mood
                state.pending_count = 1

            state.derived_from = derived_json
            await db.commit()

    except Exception as e:
        logger.warning("情绪状态更新失败", error=str(e), user_id=user_id)
