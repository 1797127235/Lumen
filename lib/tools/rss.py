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
            description="添加 RSS 订阅源。当用户想要订阅某个博客、新闻源、播客时使用。",
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
            description="删除 RSS 订阅源。",
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
            description="列出所有 RSS 订阅源。",
            input_schema={
                "type": "object",
                "properties": {},
            },
            execute=_rss_list_feeds,
            read_only=True,
            meta=ToolMeta(risk="read-only", always_on=False),
        ),
        ToolDef(
            name="rss_poll",
            description="拉取所有 RSS 订阅源的新内容。手动触发拉取时使用。",
            input_schema={
                "type": "object",
                "properties": {},
            },
            execute=_rss_poll,
            read_only=True,
            meta=ToolMeta(
                risk="read-only",
                always_on=False,
                search_hint="拉取RSS、检查更新、poll feeds",
            ),
        ),
    ]
