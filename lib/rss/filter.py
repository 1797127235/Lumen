"""RSS 过滤引擎 — FOCUS.md + LLM 相关性判断 + 关键词兜底。"""

from __future__ import annotations

import json
import re

import litellm

from core.config import build_llm_call_params, get_settings
from shared.logging import get_logger

logger = get_logger(__name__)


def _get_filter_model() -> str:
    """获取过滤用的模型名称。空则回退到主模型。"""
    settings = get_settings()
    return settings.rss_filter_model or settings.llm_model


def build_filter_prompt(items: list[dict], focus_content: str) -> str:
    """构建 LLM 过滤 prompt。"""
    items_json = json.dumps(
        [
            {"event_id": i.get("event_id", ""), "title": i.get("title", ""), "content": i.get("content", "")[:200]}
            for i in items
        ],
        ensure_ascii=False,
        indent=2,
    )
    return f"""你是一个信息过滤器。根据用户的当前关注点，判断以下 RSS 条目是否相关。

【用户关注点】
{focus_content}

【RSS 条目】
{items_json}

对每个条目，返回 JSON 数组：
- event_id: 条目的 event_id
- relevant: true/false
- reason: 一句话说明为什么相关/不相关

只返回 JSON 数组，不要其他内容。"""


def parse_filter_result(llm_output: str, items: list[dict]) -> list[dict]:
    """解析 LLM 过滤结果，返回相关条目列表。"""
    # 尝试从 LLM 输出中提取 JSON
    try:
        # 去掉可能的 markdown 代码块标记
        cleaned = re.sub(r"```json\s*", "", llm_output)
        cleaned = re.sub(r"```\s*", "", cleaned)
        cleaned = cleaned.strip()

        results = json.loads(cleaned)
        if not isinstance(results, list):
            return []

        relevant_ids: set[str] = set()
        for r in results:
            if isinstance(r, dict) and r.get("relevant") is True:
                eid = r.get("event_id", "")
                if eid:
                    relevant_ids.add(eid)

        return [item for item in items if item.get("event_id", "") in relevant_ids]
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse LLM filter result: %s", e)
        return []


async def filter_by_relevance(items: list[dict], focus_content: str) -> list[dict]:
    """用 LLM 判断条目与 FOCUS.md 的相关性。

    Returns:
        相关条目列表（保持原始顺序）。
    """
    if not items:
        return []
    if not focus_content.strip():
        return []

    prompt = build_filter_prompt(items, focus_content)
    model = _get_filter_model()
    llm_params = build_llm_call_params(model=model)

    kwargs: dict = {
        "model": llm_params["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 2000,
        "api_key": llm_params["api_key"],
        "stream": False,
        "timeout": 30,
    }
    if llm_params["base_url"]:
        kwargs["base_url"] = llm_params["base_url"]

    result = await litellm.acompletion(**kwargs)

    return parse_filter_result(result.choices[0].message.content or "", items)  # type: ignore


def keyword_fallback(items: list[dict], focus_content: str) -> list[dict]:
    """关键词匹配兜底（LLM 过滤失败时使用）。

    从 FOCUS.md 提取长度 > 2 的词做 substring 匹配。
    """
    keywords = [w.strip().lower() for w in focus_content.split() if len(w.strip()) > 2]
    if not keywords:
        return []

    return [
        item
        for item in items
        if any(kw in item.get("title", "").lower() or kw in item.get("content", "").lower() for kw in keywords)
    ]


def format_push_message(item: dict) -> str:
    """格式化推送消息。"""
    title = item.get("title", "无标题")
    url = item.get("url", "")
    source = item.get("source_name", "")
    summary = item.get("content", "")
    # 截断过长的摘要
    if len(summary) > 300:
        summary = summary[:300] + "…"

    parts = [f"📰 {title}"]
    if summary:
        parts.append(f"\n{summary}")
    parts.append(f"\n🔗 {url}")
    if source:
        parts.append(f"\n📂 {source}")
    return "".join(parts)
