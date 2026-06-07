"""RSS 订阅工具 — 管理和拉取 RSS 订阅源。"""

from __future__ import annotations

from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok

# ── 工具执行函数 ───────────────────────────────────────────────────


async def _rss_add_feed(args: dict[str, Any], deps):
    from lib.rss.feed_service import add_feed

    name = args.get("name", "").strip()
    url = args.get("url", "").strip()
    category = args.get("category", "").strip()

    if not name or not url:
        return tool_error("请提供订阅源名称(name)和地址(url)")

    result = await add_feed(name, url, category)
    if "error" in result:
        return tool_error(result["error"])

    return tool_ok(f"✅ 已添加订阅源「{name}」({url})")


async def _rss_remove_feed(args: dict[str, Any], deps):
    from lib.rss.feed_service import remove_feed

    name = args.get("name", "").strip()
    if not name:
        return tool_error("请提供要删除的订阅源名称(name)")

    await remove_feed(name)
    return tool_ok(f"✅ 已删除订阅源「{name}」")


async def _rss_list_feeds(args: dict[str, Any], deps):
    from lib.rss.feed_service import list_feeds

    feeds = await list_feeds()
    if not feeds:
        return tool_ok("当前没有任何订阅源。使用 rss_add_feed 添加。")

    lines = [f"共 {len(feeds)} 个订阅源：\n"]
    for i, f in enumerate(feeds, 1):
        status = "✅" if f.get("enabled", True) else "⏸️"
        cat = f" [{f['category']}]" if f.get("category") else ""
        lines.append(f"{i}. {status} **{f['name']}**{cat}")
        lines.append(f"   {f['url']}")
    return tool_ok("\n".join(lines))


async def _rss_list_items(args: dict[str, Any], deps):
    from lib.rss.feed_service import list_items

    source_name = args.get("source_name", "").strip()
    limit = min(int(args.get("limit", 20)), 50)
    unread_only = args.get("unread_only", False)

    items = await list_items(source_name=source_name, limit=limit, unread_only=unread_only)
    if not items:
        msg = "当前没有缓存的 RSS 条目。" if not source_name else f"订阅源「{source_name}」没有缓存条目。"
        return tool_ok(msg)

    total_hint = f"（{source_name}）" if source_name else ""
    lines = [f"📰 RSS 缓存条目{total_hint}，显示最新 {len(items)} 条：\n"]
    for i, item in enumerate(items, 1):
        title = item.get("title", "无标题")
        url = item.get("url", "")
        source = item.get("source_name", "")
        published = item.get("published_at", "")
        time_hint = f" ({published[:10]})" if published else ""
        lines.append(f"{i}. **{title}**{time_hint}")
        if url:
            lines.append(f"   {url}")
        if source:
            lines.append(f"   来源：{source}")

    return tool_ok("\n".join(lines))


async def _rss_poll(args: dict[str, Any], deps):
    from lib.rss.feed_service import get_unread_events, poll_feeds

    result = await poll_feeds()
    if result.get("new_items", 0) == 0:
        return tool_ok(f"本轮无新内容（已缓存 {result.get('total_items', 0)} 条）")

    unreads = await get_unread_events()
    lines = [f"📰 拉取完成：新增 {result['new_items']} 条，未读 {len(unreads)} 条\n"]
    for item in unreads[:10]:
        lines.append(f"- **{item.get('title', '无标题')}**")
        lines.append(f"  {item.get('url', '')}")
        source = item.get("source_name", "")
        if source:
            lines.append(f"  来源：{source}")

    return tool_ok("\n".join(lines))


# ── 工具注册 ──────────────────────────────────────────────────────


def create_rss_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="rss_add_feed",
            description=(
                "添加 RSS 订阅源。用户提供博客、新闻源、播客等 feed 地址并要求订阅时使用。\n\n"
                "典型触发：「帮我订阅 xxx」「添加这个 RSS」「关注这个博客」。\n"
                "不要用于：查看新闻内容（用 rss_list_items）、刷新订阅（用 rss_poll）。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "订阅源名称，如 'Simon Willison'"},
                    "url": {"type": "string", "description": "RSS/Atom feed 地址"},
                    "category": {"type": "string", "description": "分类标签（可选）", "default": ""},
                },
                "required": ["name", "url"],
            },
            execute=_rss_add_feed,
            read_only=False,
            meta=ToolMeta(
                risk="write",
                always_on=False,
                search_hint="订阅RSS、添加订阅源、subscribe",
            ),
        ),
        ToolDef(
            name="rss_remove_feed",
            description="删除 RSS 订阅源。用户提供订阅源名称并要求取消订阅时使用。",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的订阅源名称"},
                },
                "required": ["name"],
            },
            execute=_rss_remove_feed,
            read_only=False,
            meta=ToolMeta(risk="write", always_on=False),
        ),
        ToolDef(
            name="rss_list_feeds",
            description=(
                "列出当前所有 RSS 订阅源及其状态。\n\n"
                "典型触发：「我订阅了什么」「有哪些 RSS」「列出订阅源」。\n"
                "返回：订阅源名称、URL、启用状态。"
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
            execute=_rss_list_feeds,
            read_only=True,
            meta=ToolMeta(risk="read-only", always_on=True),
        ),
        ToolDef(
            name="rss_poll",
            description=(
                "拉取所有 RSS 订阅源的最新内容。从远程服务器同步新条目到本地缓存。\n\n"
                "典型触发：「刷新 RSS」「检查有没有新文章」「拉取最新内容」。\n\n"
                "⚠️ 调用此工具前不需要先查看订阅源列表或翻文件系统，直接调用即可。\n"
                "返回：新增条目数量 + 未读条目摘要。"
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
            execute=_rss_poll,
            read_only=True,
            meta=ToolMeta(
                risk="read-only",
                always_on=True,
                search_hint="拉取RSS、检查更新、poll feeds",
            ),
        ),
        ToolDef(
            name="rss_list_items",
            description=(
                "查询本地已缓存的 RSS 新闻条目。这是获取 RSS 内容的首选工具。\n\n"
                "典型触发：「有什么新闻」「看看 RSS」「新华社有什么更新」「最近的文章」。\n"
                "参数：source_name 按订阅源名称过滤，limit 控制返回条数。\n\n"
                "⚠️ 重要：用户问新闻/资讯/RSS 内容时，直接调用此工具。\n"
                "不要用 file_ls/file_read/shell 翻文件系统或查数据库来获取 RSS 数据。\n"
                "不要用 web_search 搜索新闻——RSS 条目已缓存在此工具中。\n"
                "返回：标题、链接、来源、发布时间。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "source_name": {
                        "type": "string",
                        "description": "按订阅源名称过滤（如 '新华社 B站'），留空返回全部",
                        "default": "",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回条数（默认 20，最大 50）",
                        "default": 20,
                    },
                },
            },
            execute=_rss_list_items,
            read_only=True,
            meta=ToolMeta(
                risk="read-only",
                always_on=True,
                search_hint="查看RSS内容、最新条目、RSS列表",
            ),
        ),
    ]
