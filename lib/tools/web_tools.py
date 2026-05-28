"""Web 工具集 — 多后端搜索 + 网页提取 + LLM 智能压缩。

移植自 hermes-agent/tools/web_tools.py，适配 Lumen 工具接口。
支持后端：Exa / Tavily / Serper / Brave / SearXNG / DuckDuckGo
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 25.0
_DEFAULT_MAX_RESULTS = 5
_MAX_CONTENT_SIZE = 2_000_000  # 2MB - 拒绝处理
_CHUNK_THRESHOLD = 500_000     # 500K - 分块处理
_CHUNK_SIZE = 100_000          # 每块 100K
_MAX_OUTPUT_SIZE = 5000        # 最终输出上限


# ═══════════════════════════════════════════════════════════════════
#  后端检测
# ═══════════════════════════════════════════════════════════════════


def _has_env(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _get_search_backend() -> str:
    """自动检测可用的搜索后端。"""
    from core.config import get_settings
    settings = get_settings()

    # 优先用 Lumen 配置
    if settings.search_provider and settings.search_api_key:
        return settings.search_provider

    # 自动检测环境变量
    candidates = [
        ("exa", _has_env("EXA_API_KEY")),
        ("tavily", _has_env("TAVILY_API_KEY")),
        ("brave", _has_env("BRAVE_SEARCH_API_KEY")),
        ("serper", _has_env("SERPER_API_KEY")),
        ("searxng", _has_env("SEARXNG_URL")),
    ]
    for backend, available in candidates:
        if available:
            return backend

    return ""


def _get_extract_backend() -> str:
    """自动检测可用的网页提取后端。"""
    if _has_env("FIRECRAWL_API_KEY"):
        return "firecrawl"
    if _has_env("EXA_API_KEY"):
        return "exa"
    return "httpx"  # 默认用 httpx 直接抓取


# ═══════════════════════════════════════════════════════════════════
#  搜索后端实现
# ═══════════════════════════════════════════════════════════════════


async def _search_exa(query: str, max_results: int, api_key: str) -> list[dict]:
    """Exa 搜索（通过 MCP 公开端点，无需 API Key 也可用）。"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {
                "query": query,
                "numResults": max_results,
                "livecrawl": "fallback",
                "type": "auto",
            },
        },
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        res = await client.post(
            "https://mcp.exa.ai/mcp",
            json=payload,
            headers={"accept": "application/json, text/event-stream", "content-type": "application/json"},
        )
        res.raise_for_status()

    # 解析 SSE 响应
    for line in res.text.splitlines():
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                content = data.get("result", {}).get("content", [])
                if content:
                    text = content[0].get("text", "")
                    if text:
                        parsed = json.loads(text)
                        results = parsed.get("results", [])
                        return [
                            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("text", "")[:200]}
                            for r in results[:max_results]
                        ]
            except (json.JSONDecodeError, KeyError):
                continue
    return []


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
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:200]}
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
        {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")[:200]}
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
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")[:200]}
        for r in data.get("web", {}).get("results", [])[:max_results]
    ]


async def _search_searxng(query: str, max_results: int, base_url: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        res = await client.get(
            f"{base_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "pageno": 1},
        )
        res.raise_for_status()
        data = res.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:200]}
        for r in data.get("results", [])[:max_results]
    ]


async def _search_ddgs(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo 搜索（免费，无需 API Key）。"""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        raise RuntimeError("请安装 duckduckgo-search: pip install duckduckgo-search")

    # duckduckgo-search 的异步支持有限，用同步包装
    import asyncio
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        None,
        lambda: list(DDGS().text(query, max_results=max_results))
    )
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")[:200]}
        for r in results
    ]


async def _do_search(query: str, max_results: int) -> list[dict]:
    """统一搜索入口，自动选择后端。"""
    backend = _get_search_backend()

    if backend == "exa":
        api_key = os.getenv("EXA_API_KEY", "")
        return await _search_exa(query, max_results, api_key)

    if backend == "tavily":
        api_key = os.getenv("TAVILY_API_KEY", "")
        return await _search_tavily(query, max_results, api_key)

    if backend == "serper":
        api_key = os.getenv("SERPER_API_KEY", "")
        return await _search_serper(query, max_results, api_key)

    if backend == "brave":
        api_key = os.getenv("BRAVE_SEARCH_API_KEY", "")
        return await _search_brave(query, max_results, api_key)

    if backend == "searxng":
        base_url = os.getenv("SEARXNG_URL", "")
        return await _search_searxng(query, max_results, base_url)

    if backend == "ddgs":
        return await _search_ddgs(query, max_results)

    # 无后端可用，尝试 Exa 公开端点
    return await _search_exa(query, max_results, "")


# ═══════════════════════════════════════════════════════════════════
#  网页提取
# ═══════════════════════════════════════════════════════════════════


async def _fetch_with_httpx(url: str) -> dict[str, Any]:
    """用 httpx 直接抓取网页。"""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        res = await client.get(url, headers={"User-Agent": "Lumen/1.0"})
        res.raise_for_status()

    content_type = res.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return {"error": f"不支持的内容类型：{content_type}", "url": url}

    # 简单 HTML → 文本转换
    text = res.text
    if "text/html" in content_type:
        try:
            from lxml import html as lxml_html
            doc = lxml_html.fromstring(text)
            for tag in ("script", "style", "noscript"):
                for el in doc.xpath(f"//{tag}"):
                    el.getparent().remove(el)
            text = " ".join(doc.text_content().split())
        except Exception:
            # fallback: 简单去标签
            import re
            text = re.sub(r"<[^>]+>", " ", text)
            text = " ".join(text.split())

    return {"url": url, "content": text, "title": ""}


async def _fetch_with_exa(url: str, api_key: str) -> dict[str, Any]:
    """用 Exa 提取网页内容。"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_extract",
            "arguments": {"urls": [url]},
        },
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        res = await client.post(
            "https://mcp.exa.ai/mcp",
            json=payload,
            headers={"accept": "application/json, text/event-stream", "content-type": "application/json"},
        )
        res.raise_for_status()

    for line in res.text.splitlines():
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                content = data.get("result", {}).get("content", [])
                if content:
                    text = content[0].get("text", "")
                    if text:
                        parsed = json.loads(text)
                        results = parsed.get("results", [])
                        if results:
                            return {
                                "url": url,
                                "content": results[0].get("text", ""),
                                "title": results[0].get("title", ""),
                            }
            except (json.JSONDecodeError, KeyError):
                continue
    return {"error": "Exa 提取失败", "url": url}


async def _do_extract(url: str) -> dict[str, Any]:
    """统一提取入口。"""
    backend = _get_extract_backend()

    if backend == "firecrawl":
        api_key = os.getenv("FIRECRAWL_API_KEY", "")
        if api_key:
            try:
                # firecrawl SDK 版本差异较大，用 httpx 直接调 API 更稳定
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    res = await client.post(
                        "https://api.firecrawl.dev/v1/scrape",
                        json={"url": url, "formats": ["markdown"]},
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    res.raise_for_status()
                    data = res.json()
                result = data.get("data", {})
                return {
                    "url": url,
                    "content": result.get("markdown", ""),
                    "title": result.get("metadata", {}).get("title", ""),
                }
            except Exception as e:
                logger.warning("firecrawl extract failed", error=str(e))

    if backend == "exa":
        api_key = os.getenv("EXA_API_KEY", "")
        if api_key:
            return await _fetch_with_exa(url, api_key)

    return await _fetch_with_httpx(url)


# ═══════════════════════════════════════════════════════════════════
#  LLM 内容压缩
# ═══════════════════════════════════════════════════════════════════


async def _compress_with_llm(content: str, url: str = "", title: str = "") -> str | None:
    """用 LLM 压缩大网页内容。"""
    if len(content) < 5000:
        return None  # 太短，不压缩

    context_info = []
    if title:
        context_info.append(f"标题：{title}")
    if url:
        context_info.append(f"来源：{url}")
    context_str = "\n".join(context_info) + "\n\n" if context_info else ""

    prompt = f"""请将以下网页内容压缩为简洁的 Markdown 摘要，保留所有关键信息。

{context_str}内容：
{content[:50000]}

要求：
1. 保留关键事实、数据、代码片段
2. 用 Markdown 格式化（标题、列表、强调）
3. 输出不超过 3000 字符"""

    try:
        import litellm
        from core.config import build_llm_call_params

        llm_params = build_llm_call_params()
        kwargs: dict = {
            "model": llm_params["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 4000,
            "api_key": llm_params["api_key"],
            "stream": False,
            "timeout": 60,
        }
        if llm_params["base_url"]:
            kwargs["base_url"] = llm_params["base_url"]

        response = await litellm.acompletion(**kwargs)
        result = response.choices[0].message.content  # type: ignore
        if result and len(result) < len(content):
            return result.strip()
    except Exception as e:
        logger.warning("LLM compression failed", error=str(e))

    return None


async def _process_large_content(content: str, url: str = "", title: str = "") -> str:
    """处理大内容：分块压缩 + 合成。"""
    if len(content) <= _CHUNK_THRESHOLD:
        # 单次压缩
        result = await _compress_with_llm(content, url, title)
        return result or content[:_MAX_OUTPUT_SIZE]

    # 分块处理
    chunks = [content[i:i + _CHUNK_SIZE] for i in range(0, len(content), _CHUNK_SIZE)]
    logger.info("chunked processing", chunks=len(chunks), total_chars=len(content))

    async def summarize_chunk(idx: int, chunk: str) -> tuple[int, str | None]:
        try:
            result = await _compress_with_llm(chunk, url, f"{title} (第{idx+1}块)")
            return idx, result
        except Exception:
            return idx, None

    tasks = [summarize_chunk(i, chunk) for i, chunk in enumerate(chunks)]
    results = await asyncio.gather(*tasks)

    summaries = []
    for idx, summary in sorted(results, key=lambda x: x[0]):
        if summary:
            summaries.append(f"## 第{idx+1}部分\n{summary}")

    if not summaries:
        return content[:_MAX_OUTPUT_SIZE]

    combined = "\n\n".join(summaries)
    if len(combined) <= _MAX_OUTPUT_SIZE:
        return combined

    # 最终压缩
    final = await _compress_with_llm(combined, url, title)
    return final or combined[:_MAX_OUTPUT_SIZE]


# ═══════════════════════════════════════════════════════════════════
#  工具执行函数
# ═══════════════════════════════════════════════════════════════════


async def _web_search_v2(args: dict[str, Any], deps):
    """增强版搜索：自动选择后端，支持更多来源。"""
    query = args.get("query", "").strip()
    max_results = min(int(args.get("max_results", _DEFAULT_MAX_RESULTS)), 20)

    if not query:
        return tool_error("请提供搜索关键词")

    try:
        results = await asyncio.wait_for(
            _do_search(query, max_results),
            timeout=_TIMEOUT + 5,
        )
    except TimeoutError:
        return tool_error("搜索超时，请稍后重试")
    except Exception as e:
        logger.warning("web_search failed", error=str(e))
        return tool_error(f"搜索失败：{e}")

    if not results:
        return tool_ok(f"未找到关于「{query}」的相关结果。")

    backend = _get_search_backend() or "exa"
    lines = [f"搜索「{query}」，来源：{backend}，共 {len(results)} 条结果：\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['url']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        lines.append("")
    return tool_ok("\n".join(lines), backend=backend, results=len(results))


async def _web_extract(args: dict[str, Any], deps):
    """提取网页内容，支持 LLM 智能压缩。"""
    urls = args.get("urls", [])
    use_compression = args.get("use_compression", True)

    if not urls:
        return tool_error("请提供要提取的 URL 列表")
    if not isinstance(urls, list):
        urls = [urls]

    results = []
    for url in urls[:5]:  # 最多 5 个 URL
        try:
            data = await asyncio.wait_for(_do_extract(url), timeout=_TIMEOUT)
        except TimeoutError:
            results.append({"url": url, "error": "提取超时"})
            continue
        except Exception as e:
            results.append({"url": url, "error": str(e)})
            continue

        if "error" in data:
            results.append(data)
            continue

        content = data.get("content", "")
        title = data.get("title", "")

        # 大内容压缩
        if use_compression and len(content) > 5000:
            compressed = await _process_large_content(content, url, title)
            content = compressed

        results.append({
            "url": url,
            "title": title,
            "content": content[:_MAX_OUTPUT_SIZE],
            "original_length": len(data.get("content", "")),
        })

    # 格式化输出
    lines = []
    for i, r in enumerate(results, 1):
        if "error" in r:
            lines.append(f"{i}. ❌ {r['url']}: {r['error']}")
        else:
            lines.append(f"{i}. **{r.get('title', r['url'])}**")
            lines.append(f"   来源：{r['url']}")
            if r.get("original_length", 0) > len(r.get("content", "")):
                lines.append(f"   （已压缩：{r['original_length']} → {len(r['content'])} 字符）")
            lines.append(f"\n{r['content']}")
        lines.append("\n---\n")

    return tool_ok("\n".join(lines), urls_processed=len(results))


async def _web_crawl(args: dict[str, Any], deps):
    """爬取网站内容（简化版，仅支持单页）。"""
    url = args.get("url", "").strip()
    instruction = args.get("instruction", "")

    if not url:
        return tool_error("请提供要爬取的 URL")

    # 提取内容
    try:
        data = await asyncio.wait_for(_do_extract(url), timeout=_TIMEOUT)
    except TimeoutError:
        return tool_error("爬取超时")
    except Exception as e:
        return tool_error(f"爬取失败：{e}")

    if "error" in data:
        return tool_error(data["error"])

    content = data.get("content", "")
    title = data.get("title", "")

    # 如果有指令，用 LLM 处理
    if instruction and len(content) > 1000:
        prompt = f"""根据以下指令处理网页内容：

指令：{instruction}

网页内容：
{content[:30000]}

请按指令提取或总结信息。"""

        try:
            import litellm
            from core.config import build_llm_call_params

            llm_params = build_llm_call_params()
            kwargs: dict = {
                "model": llm_params["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 4000,
                "api_key": llm_params["api_key"],
                "stream": False,
                "timeout": 60,
            }
            if llm_params["base_url"]:
                kwargs["base_url"] = llm_params["base_url"]

            response = await litellm.acompletion(**kwargs)
            result = response.choices[0].message.content  # type: ignore
            if result:
                content = result.strip()
        except Exception as e:
            logger.warning("crawl LLM processing failed", error=str(e))

    return tool_ok(
        f"**{title or url}**\n\n{content[:_MAX_OUTPUT_SIZE]}",
        url=url,
        title=title,
    )


# ═══════════════════════════════════════════════════════════════════
#  工具注册
# ═══════════════════════════════════════════════════════════════════


def create_web_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="web_search",
            description=(
                "搜索互联网获取最新信息。支持多个搜索后端（Exa、Tavily、Brave、Serper、SearXNG、DuckDuckGo）自动切换。"
                "当用户问到新闻、近期事件、实时数据、或你知识截止日期之后的内容时使用。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回结果数，默认 5，最多 20",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            execute=_web_search_v2,
            read_only=True,
            meta=ToolMeta(
                always_on=False,
                risk="read-only",
                search_hint="搜索网页、查资料、google、百度、exa、tavily",
                tags=["web", "search", "internet"],
            ),
        ),
        ToolDef(
            name="web_extract",
            description=(
                "提取网页完整内容。支持 LLM 智能压缩（大网页自动摘要，减少 token 消耗）。"
                "适合需要详细阅读网页内容的场景，如文章、文档、产品页等。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要提取的 URL 列表（最多 5 个）",
                    },
                    "use_compression": {
                        "type": "boolean",
                        "description": "是否启用 LLM 压缩（大网页自动摘要），默认 true",
                        "default": True,
                    },
                },
                "required": ["urls"],
            },
            execute=_web_extract,
            read_only=True,
            meta=ToolMeta(
                always_on=False,
                risk="read-only",
                search_hint="抓取网页、提取内容、读取文章、fetch",
                tags=["web", "extract", "fetch", "scrape"],
            ),
        ),
        ToolDef(
            name="web_crawl",
            description=(
                "爬取网页内容并按指令处理。可以提取特定信息、总结内容、或按格式整理。"
                "适合需要从网页中提取结构化信息的场景。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要爬取的 URL"},
                    "instruction": {
                        "type": "string",
                        "description": "处理指令（可选），如'提取所有价格信息'、'总结主要观点'",
                    },
                },
                "required": ["url"],
            },
            execute=_web_crawl,
            read_only=True,
            meta=ToolMeta(
                always_on=False,
                risk="read-only",
                search_hint="爬取网站、提取结构化信息、crawl",
                tags=["web", "crawl", "extract"],
            ),
        ),
    ]
