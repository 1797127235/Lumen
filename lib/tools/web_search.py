"""网络搜索工具 — 支持 Tavily / Serper / Brave。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from lib.tools._base import ToolDef, ToolMeta, tool_error
from shared.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 20.0
_DEFAULT_MAX_RESULTS = 5


# ── Provider 实现 ──────────────────────────────────────────────────


async def _search_tavily(query: str, max_results: int, api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        res = await client.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": max_results, "search_depth": "basic"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        res.raise_for_status()
        data = res.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])
    ]


async def _search_serper(query: str, max_results: int, api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        res = await client.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": max_results},
            headers={"X-API-KEY": api_key},
        )
        res.raise_for_status()
        data = res.json()
    return [
        {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
        for r in data.get("organic", [])[:max_results]
    ]


async def _search_brave(query: str, max_results: int, api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        res = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        res.raise_for_status()
        data = res.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
        for r in data.get("web", {}).get("results", [])[:max_results]
    ]


_PROVIDERS = {
    "tavily": _search_tavily,
    "serper": _search_serper,
    "brave": _search_brave,
}


# ── 工具执行函数 ───────────────────────────────────────────────────


async def _web_search(args: dict[str, Any], deps) -> str:
    from core.config import get_settings

    query = args.get("query", "").strip()
    max_results = min(int(args.get("max_results", _DEFAULT_MAX_RESULTS)), 10)

    if not query:
        return tool_error("请提供搜索关键词")

    settings = get_settings()
    provider = settings.search_provider
    api_key = settings.search_api_key

    if not provider:
        return tool_error("未配置搜索 provider，请在 .env 中设置 SEARCH_PROVIDER（tavily / serper / brave）")
    if provider not in _PROVIDERS:
        return tool_error(f"不支持的搜索 provider：{provider}，可选：tavily / serper / brave")
    if not api_key:
        return tool_error("未配置搜索 API Key，请在 .env 中设置 SEARCH_API_KEY")

    try:
        results = await asyncio.wait_for(
            _PROVIDERS[provider](query, max_results, api_key),
            timeout=_TIMEOUT + 5,
        )
    except TimeoutError:
        return tool_error("搜索超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        return tool_error(f"搜索 API 返回错误：{e.response.status_code}")
    except Exception as e:
        logger.warning("web_search failed", provider=provider, error=str(e))
        return tool_error(f"搜索失败：{e}")

    if not results:
        return f"未找到关于「{query}」的相关结果。"

    lines = [f"搜索「{query}」，来源：{provider}，共 {len(results)} 条结果：\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet'][:200]}")
        lines.append("")
    return "\n".join(lines)


# ── 工具注册 ──────────────────────────────────────────────────────


def create_web_search_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="web_search",
            description=(
                "搜索互联网获取最新信息。当用户问到新闻、近期事件、实时数据、" "或你知识截止日期之后的内容时使用。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回结果数，默认 5，最多 10",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            execute=_web_search,
            read_only=True,
            meta=ToolMeta(always_on=False, risk="read-only", search_hint="搜索网页、查资料、google、百度"),
        )
    ]
