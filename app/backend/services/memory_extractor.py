"""Extract long-term memory events from a chat turn."""

from __future__ import annotations

import json
import logging

from app.backend.agent.llm_router import chat as llm_chat

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """从以下对话中提取适合长期记忆的新增信息，只返回 JSON 数组。

用户：{user_input}
AI：{assistant_reply}

格式：
[
  {{
    "event_type": "profile_updated|skill_added|skill_level_changed|goal_updated|preference_learned|decision_made|status_changed|experience_added",
    "entity_type": "profile|skill|goal|preference|decision|status|experience",
    "entity_id": "可选实体ID",
    "payload": {{}},
    "source": "conversation",
    "confidence": 0.0
  }}
]

如果没有新增信息，返回 []。不要输出任何解释。
"""


async def extract_memory_from_conversation(
    user_id: str,
    conversation_id: str,
    user_input: str,
    assistant_reply: str,
) -> list[dict]:
    del user_id, conversation_id
    try:
        prompt = _EXTRACT_PROMPT.format(
            user_input=user_input,
            assistant_reply=assistant_reply,
        )
        result = await llm_chat(
            task_type="memory_extract",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
        )

        if isinstance(result, str):
            json_start = result.find("[")
            json_end = result.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                result = result[json_start:json_end]
            events = json.loads(result)
        else:
            events = result

        filtered_events = []
        for event in events:
            if event.get("confidence", 0) < 0.7:
                continue
            if not event.get("event_type"):
                continue
            filtered_events.append(event)

        return filtered_events
    except json.JSONDecodeError as exc:
        logger.warning("Conversation memory JSON parse failed: %s", exc)
        return []
    except Exception as exc:
        logger.error("Conversation memory extraction failed: %s", exc)
        return []


async def save_extracted_events(
    user_id: str,
    conversation_id: str,
    events: list[dict],
) -> int:
    del conversation_id
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
    events = await extract_memory_from_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
        user_input=user_input,
        assistant_reply=assistant_reply,
    )
    return await save_extracted_events(
        user_id=user_id,
        conversation_id=conversation_id,
        events=events,
    )
