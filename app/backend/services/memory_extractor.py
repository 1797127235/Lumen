"""Memory Extractor — 对话后自动提取长期记忆

职责：
- 判断本轮是否包含值得长期记忆的信息
- 调用 LLM 输出结构化事件列表
- 过滤低置信度、空事件、重复事件
- 调用 growth_event_service.create_growth_event() 写入
"""

from __future__ import annotations

import json
import logging

from app.backend.agent.llm_router import chat as llm_chat

logger = logging.getLogger(__name__)

# 提取 prompt
_EXTRACT_PROMPT = """从以下对话中提取关键信息，用于长期记忆。

用户：{user_input}
AI：{assistant_reply}

提取格式（只提取有新信息的部分，返回 JSON 数组）：
[
  {{
    "event_type": "profile_updated|skill_added|skill_level_changed|goal_updated|preference_learned|decision_made|status_changed|experience_added",
    "entity_type": "profile|skill|goal|preference|decision|status|experience",
    "entity_id": "实体ID（可选）",
    "payload": {{
      "具体信息": "值"
    }},
    "source": "conversation",
    "confidence": 0.0-1.0
  }}
]

事件类型说明：
- profile_updated: 学校、专业、年级、目标方向等基本信息变化
- skill_added: 新增技能
- skill_level_changed: 技能熟练度变化
- goal_updated: 职业目标、学习目标变化
- preference_learned: 学习风格、交互偏好、岗位偏好
- decision_made: 用户做出重要决策
- status_changed: 求职、学习、心理状态变化
- experience_added: 项目、实习、竞赛经历

如果没有新信息，返回空数组 []。
只返回 JSON，不要其他文字。"""


async def extract_memory_from_conversation(
    user_id: str,
    conversation_id: str,
    user_input: str,
    assistant_reply: str,
) -> list[dict]:
    """从对话中提取长期记忆

    Args:
        user_id: 用户 ID
        conversation_id: 对话 ID
        user_input: 用户输入
        assistant_reply: AI 回复

    Returns:
        提取的事件列表
    """
    try:
        # 调用 LLM 提取
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

        # 解析 JSON
        if isinstance(result, str):
            # 尝试提取 JSON
            json_start = result.find("[")
            json_end = result.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                result = result[json_start:json_end]
            events = json.loads(result)
        else:
            events = result

        # 过滤
        filtered_events = []
        for event in events:
            # 过滤低置信度
            if event.get("confidence", 0) < 0.7:
                continue
            # 过滤空事件
            if not event.get("event_type"):
                continue
            filtered_events.append(event)

        logger.info(
            "对话记忆提取: user_id=%s, conversation_id=%s, total=%d, filtered=%d",
            user_id,
            conversation_id,
            len(events),
            len(filtered_events),
        )

        return filtered_events

    except json.JSONDecodeError as e:
        logger.warning("对话记忆提取 JSON 解析失败: %s", e)
        return []
    except Exception as e:
        logger.error("对话记忆提取失败: %s", e)
        return []


async def save_extracted_events(
    user_id: str,
    conversation_id: str,
    events: list[dict],
) -> int:
    """保存提取的事件到数据库

    Args:
        user_id: 用户 ID
        conversation_id: 对话 ID
        events: 事件列表

    Returns:
        成功保存的事件数量
    """
    if not events:
        return 0

    try:
        from app.backend.db.base import get_async_session_maker
        from app.backend.services.md_projector import create_event_and_project_md

        success_count = 0
        async with get_async_session_maker()() as db:
            for event in events:
                try:
                    # 使用统一入口（带去重）
                    created = await create_event_and_project_md(
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
                except Exception as e:
                    logger.warning("保存事件失败: %s", e)

            await db.commit()

        logger.info(
            "对话记忆保存: user_id=%s, conversation_id=%s, saved=%d",
            user_id,
            conversation_id,
            success_count,
        )

        return success_count

    except Exception as e:
        logger.error("对话记忆保存失败: %s", e)
        return 0


async def extract_and_save_memory(
    user_id: str,
    conversation_id: str,
    user_input: str,
    assistant_reply: str,
) -> int:
    """提取并保存对话记忆（完整流程）

    Args:
        user_id: 用户 ID
        conversation_id: 对话 ID
        user_input: 用户输入
        assistant_reply: AI 回复

    Returns:
        成功保存的事件数量
    """
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
