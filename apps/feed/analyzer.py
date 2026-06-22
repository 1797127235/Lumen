"""订阅信息流应用 — 分析引擎。

用 Lumen push 进来的关注点 + 新条目，调自己的 LLM，
对每条输出 relevance / summary / verdict，写回 analysis 表。
通过 httpx 调 OpenAI 兼容 API，不引入额外 SDK。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from config import Settings
from store import FeedStore

logger = logging.getLogger("feed.analyzer")

_SYSTEM = """你是一个 RSS 订阅分析助手。给定用户的关注点和一批订阅条目，对每条条目判断：
- relevance: 相关程度 (high / medium / low / none)
- summary: 一句话中文摘要 (<=40 字)
- verdict: 是否值得用户看 (worth_reading / skip)

只返回一个 JSON 数组，每个元素 {"id", "relevance", "summary", "verdict"}，不要任何额外文字或解释。"""


def _build_user(focus: list[str], items: list[dict[str, Any]]) -> str:
    focus_str = "、".join(focus) if focus else "(暂无明确关注点，按内容本身质量判断)"
    lines = [f"## 用户关注点\n{focus_str}", "", "## 待分析条目"]
    for it in items:
        lines.append(f"[id={it['id']}] {it.get('title', '')}\n{it.get('summary', '')[:300]}")
    return "\n\n".join(lines)


async def analyze_pending(store: FeedStore, settings: Settings) -> dict[str, Any]:
    """拉未分析条目，批量送 LLM，写回。返回 {analyzed, skipped?, error?}。"""
    if not settings.llm_api_key:
        return {"analyzed": 0, "skipped": "no_llm_key"}

    focus = await store.get_focus()
    batch = await store.get_unanalyzed_items(settings.analyze_batch_size)
    if not batch:
        return {"analyzed": 0}

    payload = [{"id": it["id"], "title": it.get("title", ""), "summary": it.get("summary", "")} for it in batch]
    try:
        results = await _call_llm(settings, focus, payload)
    except Exception as exc:
        logger.error("LLM 分析失败: %s", exc)
        return {"analyzed": 0, "error": str(exc)}

    analyzed_ids: list[str] = []
    for item in batch:
        r = results.get(item["id"])
        if r is None:
            continue
        await store.upsert_analysis(
            item["id"],
            relevance=str(r.get("relevance", "none")),
            summary=str(r.get("summary", "")),
            verdict=str(r.get("verdict", "skip")),
            focus_snapshot=focus,
        )
        analyzed_ids.append(item["id"])
    await store.mark_analyzed(analyzed_ids)
    return {"analyzed": len(analyzed_ids)}


async def _call_llm(
    settings: Settings,
    focus: list[str],
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user(focus, items)},
        ],
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"].strip()
    # 容错：剥离 markdown 代码块
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    parsed = json.loads(content)
    return {item["id"]: item for item in parsed}
