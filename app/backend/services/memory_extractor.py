"""从单轮对话中提取长期记忆事件（PydanticAI 约束输出）。"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent

from app.backend.agent.llm_router import _get_model_identifier
from app.backend.schemas.memory_events import EVENT_PAYLOAD_MAP

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """从以下对话中提取适合长期记忆的新增信息。

用户：{user_input}
AI：{assistant_reply}

如果没有新增信息，events 返回空列表。不要编造。
"""


# ── PydanticAI 结构化输出 ──


class ExtractedEvent(BaseModel):
    event_type: Literal[
        "profile_updated",
        "skill_added",
        "skill_level_changed",
        "goal_updated",
        "preference_learned",
        "decision_made",
        "status_changed",
        "experience_added",
    ]
    payload: dict = {}
    confidence: float = 0.0


class MemoryExtraction(BaseModel):
    events: list[ExtractedEvent] = []


_extract_agent = Agent(
    _get_model_identifier("memory_summarize"),
    result_type=MemoryExtraction,
    system_prompt="从对话中提取可长期存储的信息。只提取明确表达的内容，不要编造。",
)


def _validate_event_payload(event: ExtractedEvent) -> dict | None:
    """校验 payload 是否匹配对应 event_type 的 schema。不匹配则丢弃。"""
    schema = EVENT_PAYLOAD_MAP.get(event.event_type)
    if schema is None:
        return None
    try:
        validated = schema.model_validate(event.payload)
        return {
            "event_type": event.event_type,
            "payload": validated.model_dump(),
            "confidence": event.confidence,
        }
    except ValidationError:
        logger.warning("Extractor event rejected: type=%s", event.event_type)
        return None


async def extract_memory_from_conversation(
    user_input: str,
    assistant_reply: str,
) -> list[dict]:
    try:
        prompt = _EXTRACT_PROMPT.format(
            user_input=user_input,
            assistant_reply=assistant_reply,
        )
        result = await _extract_agent.run(prompt)
        events = result.data.events

        filtered_events: list[dict] = []
        for event in events:
            if event.confidence < 0.7:
                continue
            validated = _validate_event_payload(event)
            if validated is None:
                continue
            filtered_events.append(validated)

        return filtered_events
    except Exception as exc:
        logger.warning("Conversation memory extraction failed: %s", exc)
        return []


async def save_extracted_events(
    user_id: str,
    events: list[dict],
) -> int:
    if not events:
        return 0

    try:
        from app.backend.db.base import get_async_session_maker
        from app.backend.services.cognee_projector import project_event_ids
        from app.backend.services.growth_event_service import create_growth_event_with_dedup
        from app.backend.services.md_projector import sync_user_md_projection

        success_count = 0
        created_event_ids: list[str] = []
        async with get_async_session_maker()() as db:
            for event in events:
                try:
                    created = await create_growth_event_with_dedup(
                        db=db,
                        user_id=user_id,
                        event_type=event["event_type"],
                        entity_type=event.get("entity_type"),
                        entity_id=event.get("entity_id"),
                        payload=event.get("payload"),
                        source="对话识别",
                    )
                    if created:
                        success_count += 1
                        created_event_ids.append(str(created.id))
                except Exception as exc:
                    logger.warning("Save extracted event failed: %s", exc)

            await db.commit()

        if success_count > 0:
            await sync_user_md_projection(user_id)
            await project_event_ids(created_event_ids)

        logger.info("Conversation memory saved: user_id=%s, saved=%d", user_id, success_count)
        return success_count
    except Exception as exc:
        logger.error("Conversation memory save failed: %s", exc)
        return 0


async def extract_and_save_memory(
    user_id: str,
    conversation_id: str,
    user_input: str,
    assistant_reply: str,
) -> int:
    del conversation_id  # reserved for future per-conversation extraction scope
    events = await extract_memory_from_conversation(
        user_input=user_input,
        assistant_reply=assistant_reply,
    )
    return await save_extracted_events(
        user_id=user_id,
        events=events,
    )
