"""事件合并与文本格式化 — markdown.py 与 snapshot.py 的共享纯函数层。"""

from __future__ import annotations

import json
import re
from datetime import datetime

from pydantic import ValidationError

from lib.memory.models import GrowthEvent
from lib.profile.schemas import (
    DecisionPayload,
    KeyValuePayload,
)


def deep_merge(base: dict, update: dict) -> dict:
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_payload(event: GrowthEvent) -> dict:
    if not event.payload_json:
        return {}
    try:
        payload = json.loads(event.payload_json)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_profile_fields(md_text: str) -> dict:
    """从 AI 伙伴 memory.md 里提取结构化字段。"""
    patterns = {
        "name": r"- 名字[/／]昵称：(.+)",
        "bio": r"- 简介：(.+)",
    }
    fields: dict = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, md_text)
        if m:
            val = m.group(1).strip()
            if val and val != "（待填写）":
                fields[key] = val
    return fields


def merge_profile_events(events: list[GrowthEvent]) -> dict:
    """合并 profile_updated 事件为画像字典。"""
    profile: dict = {}
    for event in events:
        payload = load_payload(event)
        if not payload:
            continue
        # 新格式：{"key": xxx, "value": xxx} 或直接 {"name": xxx, "bio": xxx}
        if "key" in payload and "value" in payload:
            profile[payload["key"]] = payload["value"]
        else:
            profile = deep_merge(profile, payload)
    return profile


def merge_narrative_events(events: list[GrowthEvent]) -> list[dict]:
    """合并叙事类事件（significant_moment, reflection_added 等）为列表。"""
    result: list[dict] = []
    seen: set[str] = set()
    for event in events:
        payload = load_payload(event)
        if not payload:
            continue
        # 去重 key：title 或 key 字段
        dedup_key = payload.get("title") or payload.get("key") or ""
        if dedup_key and dedup_key in seen:
            continue
        if dedup_key:
            seen.add(dedup_key)
        result.append(
            {
                **payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )
    return result


def merge_dict_events(events: list[GrowthEvent]) -> dict:
    result: dict = {}
    for event in events:
        payload = load_payload(event)
        if not payload:
            continue
        try:
            validated = KeyValuePayload.model_validate(payload)
        except ValidationError:
            continue
        result[validated.key] = validated.value
    return result


def merge_decision_events(events: list[GrowthEvent]) -> list[dict]:
    decisions: list[dict] = []
    for event in events:
        payload = load_payload(event)
        if not payload:
            continue
        try:
            validated = DecisionPayload.model_validate(payload)
        except ValidationError:
            continue
        decisions.append(
            {
                "title": validated.title,
                "decision": validated.content,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )
    return decisions


def generate_memory_md(
    profile: dict,
    interests: dict,
    values: dict,
    preferences: dict,
    emotions: dict,
    moments: list[dict],
    decisions: list[dict],
    reflections: list[dict],
    relationships: dict,
) -> str:
    """生成 AI 伙伴画像 memory.md。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 关于你", ""]
    parts.append("> 由 Lumen 自动更新，记录 AI 对你的理解。")
    parts.append("")

    # 基础信息
    if profile:
        parts.append("## 你是谁")
        for k, v in profile.items():
            parts.append(f"- {k}：{v}")
        parts.append("")

    # 性格与兴趣
    if interests:
        parts.append("## 你真正在意的事")
        for k, v in interests.items():
            parts.append(f"- **{k}**：{v}")
        parts.append("")

    # 价值观
    if values:
        parts.append("## 你的价值观")
        for k, v in values.items():
            parts.append(f"- {k}：{v}")
        parts.append("")

    # 情绪规律
    if emotions:
        parts.append("## 情绪规律")
        for k, v in emotions.items():
            parts.append(f"- {k}：{v}")
        parts.append("")

    # 偏好
    if preferences:
        parts.append("## 偏好")
        for k, v in preferences.items():
            parts.append(f"- {k}：{v}")
        parts.append("")

    # 重要经历
    if moments:
        parts.append("## 重要经历")
        for m in moments[-5:]:
            title = m.get("title") or m.get("key") or "未命名"
            desc = m.get("description") or m.get("value") or ""
            parts.append(f"- **{title}**：{desc[:100]}")
        parts.append("")

    # 重要关系
    if relationships:
        parts.append("## 重要的人")
        for person, desc in relationships.items():
            parts.append(f"- **{person}**：{desc}")
        parts.append("")

    # 重要决策
    if decisions:
        parts.append("## 重要决策")
        for d in decisions[-5:]:
            title = d.get("title", "未命名决策")
            content = d.get("content") or d.get("decision") or ""
            parts.append(f"- **{title}**：{content[:100]}")
        parts.append("")

    # 洞察与反思
    if reflections:
        parts.append("## 你说过的关键话")
        for r in reflections[-3:]:
            insight = r.get("insight") or r.get("value") or r.get("description") or ""
            parts.append(f"> {insight[:150]}")
        parts.append("")

    parts.append("---\n*Lumen 越了解你，回应越贴近你这个人*")
    parts.append(f"*最后更新：{date_str}*")
    return "\n".join(parts)


__all__ = [
    "deep_merge",
    "extract_profile_fields",
    "generate_memory_md",
    "load_payload",
    "merge_decision_events",
    "merge_dict_events",
    "merge_narrative_events",
    "merge_profile_events",
]
