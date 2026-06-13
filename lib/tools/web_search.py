"""网络搜索工具 — 支持 Tavily / Serper / Brave。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
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


async def _web_search(args: dict[str, Any], ctx: Any = None):
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
        status_code = e.response.status_code
        if status_code == 401:
            return tool_error(
                f"搜索服务认证失败（{status_code}）：API key 无效或已过期，请检查 SEARCH_API_KEY 配置。"
                "不要重复尝试搜索，直接告知用户当前搜索不可用。"
            )
        if status_code == 429:
            return tool_error(f"搜索服务限流（{status_code}）：API 调用配额已耗尽，请稍后再试。不要重复尝试搜索。")
        return tool_error(f"搜索 API 返回错误（{status_code}）。不要重复尝试。")
    except Exception as e:
        logger.warning("web_search failed", provider=provider, error=str(e))
        return tool_error(f"搜索失败（{provider}）：{e}。不要重复尝试相同查询，换个关键词或跳过搜索。")

    if not results:
        return tool_ok(f"未找到关于「{query}」的相关结果。")

    lines = [f"搜索「{query}」，来源：{provider}，共 {len(results)} 条结果：\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet'][:200]}")
        lines.append("")
    return tool_ok("\n".join(lines), provider=provider, results=len(results))


# ── 工具注册 ──────────────────────────────────────────────────────


def create_web_search_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="web_search",
            description=(
                "搜索互联网获取最新信息。当用户问到新闻、近期事件、实时数据、或你知识截止日期之后的内容时使用。"
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
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="搜索网页、查资料、google、百度"),
        )
    ]
