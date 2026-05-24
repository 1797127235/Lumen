"""观察合成服务 — 从多类数据源提取 LLM 观察。"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select

from core.config import build_llm_call_params
from core.db import get_async_session_maker
from lib.chat.models import Conversation, Message
from lib.memory.markdown import read_memory
from lib.memory.models import GrowthEvent
from shared.logging import get_logger

logger = get_logger(__name__)


class Observation(BaseModel):
    text: str
    source_event_ids: list[str]
    source_event_types: list[str]


class ObservationsResult(BaseModel):
    observations: list[Observation]
    generated_at: datetime | None
    events_analyzed: int
    period_days: int


_cache: dict[tuple[str, int], dict] = {}

_SYSTEM_PROMPT = """你将看到 3 类关于用户的输入：
1. growth_events — 系统观察到的成长事件（可能为空）
2. recent_messages — 最近对话记录（可能为空）
3. profile — 用户画像（可能为空）

请从这些输入里综合提炼出 3 条观察。如果某一类输入为空，跳过它。如果所有输入加起来都很少（比如只有 1-2 条消息），观察可以更简短、更试探（"你刚开始用我，目前看到 X" 这种语气）。

你是 Lumen，一个本地运行的 AI 伙伴，正在为一个特定的用户合成「关于他/她的观察」。

什么叫「有价值」：
1. 指出一个模式，而不是单点事实（"你提到 X" → 不算；"你最近三次提到 X 都用了相同的句式" → 算）
2. 指向一个矛盾或漂移，而不是平铺信息（"你关心 X" → 不算；"你说不想 X 但本周主动问起了 X" → 算）
3. 引用原始证据——具体到日期和数据源类型，让用户能对号入座

什么不要做：
- 不要诊断（"你有焦虑倾向"）——你是伙伴，不是医生
- 不要建议（"你应该 X"）——只观察，不指导
- 不要泛泛而谈（"你是一个有思考的人"）——废话，删掉
- 不要超过 50 字一条
- 不要用"我注意到"开头三次——变换句式

语气：温和、克制、像一个真心在意你的朋友说出他注意到的事，不是分析师，也不是教练。"""


def _serialize_events(events: list[GrowthEvent]) -> str:
    lines = []
    for e in events:
        if not e.payload_json:
            continue
        date_str = e.created_at.strftime("%m-%d") if e.created_at else "??-??"
        lines.append(f"[{date_str}] {e.event_type} | {e.payload_json}")
    return "\n".join(lines) if lines else "（无）"


def _serialize_messages(messages: list[Message]) -> str:
    lines = []
    for m in messages:
        if not m.content:
            continue
        date_str = m.created_at.strftime("%m-%d %H:%M") if m.created_at else "??-??"
        content = m.content[:300]
        lines.append(f"[{date_str}] {m.role}: {content}")
    return "\n".join(lines) if lines else "（无）"


async def _fetch_recent_events(user_id: str, days: int, limit: int = 200) -> list[GrowthEvent]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with get_async_session_maker()() as db:
        stmt = (
            select(GrowthEvent)
            .where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.created_at >= cutoff,
                GrowthEvent.confirmation_status != "rejected",
                GrowthEvent.status == "active",
            )
            .order_by(GrowthEvent.created_at.desc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())


async def _fetch_recent_messages(user_id: str, days: int, limit: int = 100) -> list[Message]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with get_async_session_maker()() as db:
        stmt = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.conversation_id)
            .where(
                Conversation.user_id == user_id,
                Message.created_at >= cutoff,
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())


def _parse_llm_response(text: str, events: list[GrowthEvent]) -> list[Observation]:
    observations: list[Observation] = []
    blocks = re.findall(r"^\d+\.\s*(.+?)\n\s*依据：(.+)$", text.strip(), re.MULTILINE)
    for obs_text, evidence_line in blocks[:3]:
        types_found = re.findall(r"(\w+)(?:\s*\(\d{2}-\d{2}\))", evidence_line)
        source_types = list(dict.fromkeys(types_found))
        source_ids: list[str] = []
        for etype in source_types:
            dates = re.findall(rf"{re.escape(etype)}\s*\((\d{{2}}-\d{{2}})\)", evidence_line)
            for d in dates:
                for ev in events:
                    if ev.event_type == etype and ev.created_at and ev.created_at.strftime("%m-%d") == d:
                        source_ids.append(str(ev.id))
        observations.append(
            Observation(
                text=obs_text.strip(), source_event_ids=list(dict.fromkeys(source_ids)), source_event_types=source_types
            )
        )
    return observations


async def synthesize_observations(user_id: str, days: int = 7) -> ObservationsResult:
    events = await _fetch_recent_events(user_id, days)
    messages = await _fetch_recent_messages(user_id, days)
    profile_text = read_memory(user_id)[:2000]

    fingerprint = hash(
        (
            events[0].id if events else None,
            messages[0].message_id if messages else None,
            len(profile_text),
        )
    )

    cache_key = (user_id, days)
    cached = _cache.get(cache_key)
    if cached and cached.get("fingerprint") == fingerprint:
        return cached["result"]

    total_signals = len(events) + len(messages) + (1 if profile_text.strip() else 0)
    if total_signals == 0:
        result = ObservationsResult(observations=[], generated_at=None, events_analyzed=0, period_days=days)
        _cache[cache_key] = {"result": result, "fingerprint": fingerprint}
        return result

    user_prompt = f"""=== growth_events（共 {len(events)} 条）===
{_serialize_events(events)}

=== recent_messages（共 {len(messages)} 条）===
{_serialize_messages(messages)}

=== profile ===
{profile_text if profile_text.strip() else "（无）"}

请输出 3 条观察。格式严格如下，不要加其他文字：

1. <观察一，50 字以内>
   依据：<数据源类型简短描述>

2. <观察二>
   依据：...

3. <观察三>
   依据：...
"""

    try:
        import litellm

        llm_params = build_llm_call_params()
        kwargs: dict = {
            "model": llm_params["model"],
            "messages": [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
            "temperature": 0.7,
            "max_tokens": 512,
            "api_key": llm_params["api_key"],
            "stream": False,
            "timeout": 30,
        }
        if llm_params["base_url"]:
            kwargs["base_url"] = llm_params["base_url"]
        response = await litellm.acompletion(**kwargs)
        text = response.choices[0].message.content or ""
        observations = _parse_llm_response(text, events)
    except Exception as exc:
        logger.warning("观察合成失败", user_id=user_id, error=str(exc))
        observations = []

    result = ObservationsResult(
        observations=observations,
        generated_at=datetime.now(UTC) if observations else None,
        events_analyzed=total_signals,
        period_days=days,
    )
    _cache[cache_key] = {"result": result, "fingerprint": fingerprint}
    return result
